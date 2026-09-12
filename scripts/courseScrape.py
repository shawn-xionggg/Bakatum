import json
import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


class Course:
    def __init__(self, course_id, name):
        self.course_id = course_id
        self.name = name

    def get_content(self, session):
        """Top-level content nodes only (Modules and any top-level Topics)."""
        return session.get_course_content(self.course_id)

    def get_all_topics(self, session):
        """Every actual Topic (page, file, link, etc.) anywhere in the course,
        recursing through nested modules."""
        topics = []
        for node in self.get_content(session):
            topics.extend(node.get_all_topics(session))
        return topics

    def get_assignments(self, session):
        """Dropbox folders (assignments) for this course: name, due date, points."""
        return session.get_assignments(self.course_id)

    def get_grades(self, session):
        """Your own grade values for every grade item in this course."""
        return session.get_grades(self.course_id)

    def get_announcements(self, session):
        return session.get_announcements(self.course_id)

    def get_quizzes(self, session):
        return session.get_quizzes(self.course_id)

    def __str__(self):
        return f"Course(id={self.course_id}, name={self.name})"

    def __repr__(self):
        return self.__str__()


class CourseContent:
    """
    A node in a course's content tree. `content_type` is D2L's Type field:
        0 = Module (a folder that contains other Modules/Topics)
        1 = Topic  (an actual leaf item: a text page, a file, or a link)

    For Topics, `topic_type` narrows it further (per the Valence API):
        1 = File, 3 = Link, and a handful of other less common values.

    IMPORTANT: every node needs `course_id` (the org unit ID) to build valid
    API URLs — a topic/module ID alone is not enough, and mixing the two up
    (as the previous version did) silently hits the wrong org unit or 404s.
    """

    MODULE = 0
    TOPIC = 1
    FILE_TOPIC = 1     # topic_type value, distinct namespace from content_type
    LINK_TOPIC = 3

    def __init__(self, content_id, title, content_type, course_id,
                 topic_type=None, mime_type=None, url=None, parent_path=""):
        self.content_id = content_id
        self.title = title or "Untitled"
        self.content_type = content_type
        self.course_id = course_id
        self.topic_type = topic_type
        self.mime_type = mime_type
        self.url = url
        self.path = f"{parent_path}/{self.title}" if parent_path else self.title
        self._details = None  # lazy-loaded, cached full topic detail

    def is_module(self):
        return self.content_type == self.MODULE

    def is_topic(self):
        return self.content_type == self.TOPIC

    def is_file(self):
        return self.is_topic() and self.topic_type == self.FILE_TOPIC

    def is_link(self):
        return self.is_topic() and self.topic_type == self.LINK_TOPIC

    def get_children(self, session):
        """If this is a Module, fetch its direct children as CourseContent nodes."""
        if not self.is_module():
            return []

        data = session._execute_api(
            f'/d2l/api/le/{session.LE_VERSION}/{self.course_id}/content/modules/{self.content_id}/structure/'
        )
        children_raw = session._as_list(data)
        if children_raw is None:
            print(f"API Response Error or Empty (module '{self.title}'):", data)
            return []

        return [
            CourseContent(
                content_id=c.get('Id'),
                title=c.get('Title'),
                content_type=c.get('Type'),
                course_id=self.course_id,
                topic_type=c.get('TopicType'),
                mime_type=c.get('MimeType'),
                url=c.get('Url'),
                parent_path=self.path,
            )
            for c in children_raw if isinstance(c, dict)
        ]

    def get_all_topics(self, session):
        """Recursively collect every leaf Topic beneath (or including) this node."""
        if self.is_topic():
            return [self]
        topics = []
        for child in self.get_children(session):
            topics.extend(child.get_all_topics(session))
        return topics

    def get_details(self, session):
        """
        Fetch this topic's full detail object — Description (Text/Html), Url,
        LastModifiedDate, etc. Cached after the first call. Only meaningful
        for Topics, not Modules.
        """
        if not self.is_topic():
            return None
        if self._details is None:
            data = session._execute_api(
                f'/d2l/api/le/{session.LE_VERSION}/{self.course_id}/content/topics/{self.content_id}/'
            )
            self._details = data if isinstance(data, dict) else {}
        return self._details or None

    def get_text(self, session):
        """
        Best-effort inline text for this topic:
          - Text/HTML topics -> the Description field
          - Link topics      -> the target URL
          - File topics      -> None here; use Course.download_and_extract(...)
                                 (or Session.download_file + extract_text_from_pdf)
                                 to actually pull and read file contents, since
                                 that requires downloading bytes, not just
                                 fetching topic metadata.
        """
        if self.is_module():
            return None

        details = self.get_details(session)
        if not details:
            return None

        description = details.get('Description') or {}
        text = description.get('Text') or description.get('Html')
        if text:
            return text

        if self.is_link():
            return details.get('Url') or self.url

        return None  # file topics: fetch bytes separately, see note above

    def __str__(self):
        if self.is_module():
            kind = "Module"
        else:
            kind = {1: "File", 3: "Link"}.get(self.topic_type, f"Topic(type={self.topic_type})")
        return f"CourseContent(id={self.content_id}, title={self.title!r}, {kind})"

    def __repr__(self):
        return self.__str__()


class Session:
    def __init__(self):
        self.auth_file = "waterloo_auth.json"
        if not os.path.exists(self.auth_file):
            self.save_authenticated_state()

        self.BASE_URL = "https://learn.uwaterloo.ca"
        self.LP_VERSION = "1.43"   # Learning Platform (enrollments, users, orgs)
        self.LE_VERSION = "1.43"   # Learning Environment (content, grades, dropbox, news, quizzes)

        self.courses = self.get_courses()

    def save_authenticated_state(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            page.goto(self.BASE_URL)
            print("Browser opened. Log into WatIAM and complete Duo 2FA...")

            page.wait_for_url("**/d2l/home**", timeout=120000)

            print("Dashboard detected! Saving session state...")
            context.storage_state(path=self.auth_file)
            print("Saved authenticated state to waterloo_auth.json!")

            browser.close()

    # ------------------------------------------------------------------ #
    # Core request helpers
    # ------------------------------------------------------------------ #
    def _with_context(self, fn):
        """Open a browser context from saved storage_state, run fn(context), clean up."""
        if not os.path.exists(self.auth_file):
            raise FileNotFoundError("Authentication file missing. Run save_authenticated_state() first.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=self.auth_file)
            try:
                return fn(context)
            finally:
                browser.close()

    def _execute_api(self, api_path):
        if not os.path.exists(self.auth_file):
            raise FileNotFoundError("Authentication file missing. Run save_authenticated_state() first.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=self.auth_file)
            page = context.new_page()

            page.goto(f"{self.BASE_URL}/d2l/home", wait_until="networkidle")

            result = page.evaluate(f"""
                async () => {{
                    try {{
                        const res = await fetch('{api_path}');
                        if (!res.ok) return {{ error: res.status, statusText: res.statusText }};
                        return await res.json();
                    }} catch (err) {{
                        return {{ error: err.toString() }};
                    }}
                }}
            """)

            browser.close()
            return result

    def _download_binary(self, api_path, dest_path, max_bytes=None):
        """
        GET a binary endpoint (e.g. a content topic file) and save it to disk.
        If max_bytes is set, skips (without writing) files over that size,
        checking via a HEAD request first where possible.
        """
        def run(context):
            url = f"{self.BASE_URL}{api_path}"

            if max_bytes:
                try:
                    head_resp = context.request.fetch(url, method="HEAD")
                    content_length = head_resp.headers.get("content-length")
                    if content_length and int(content_length) > max_bytes:
                        return {"skipped": True, "reason": "too_large", "bytes": int(content_length)}
                except Exception:
                    pass

            resp = context.request.get(url)
            if not resp.ok:
                return {"error": resp.status, "statusText": resp.status_text}

            body = resp.body()
            if max_bytes and len(body) > max_bytes:
                return {"skipped": True, "reason": "too_large", "bytes": len(body)}

            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(body)
            return {"saved_to": dest_path, "bytes": len(body), "content_type": resp.headers.get("content-type")}

        return self._with_context(run)

    def download_file(self, course_id, topic_id, dest_folder="downloads", filename=None, max_bytes=None):
        """Download a content topic's file (PDF slides, syllabus, etc.) to dest_folder."""
        if filename is None:
            filename = f"topic_{topic_id}"
        dest_path = os.path.join(dest_folder, filename)
        return self._download_binary(
            f'/d2l/api/le/{self.LE_VERSION}/{course_id}/content/topics/{topic_id}/file',
            dest_path,
            max_bytes=max_bytes,
        )

    @staticmethod
    def _as_list(data):
        """
        Normalize an API response to a list, or None if it isn't one.

        Some D2L endpoints return a bare JSON array on success. On failure,
        _execute_api instead returns an error dict like
        {'error': 403, 'statusText': 'Forbidden'} — which is truthy, so a
        plain `if not data` check doesn't catch it, and `for item in data`
        then silently iterates over the dict's *keys* (strings like 'error')
        instead of raising immediately. Route every list-shaped response
        through this helper to catch that case early.
        """
        return data if isinstance(data, list) else None

    @staticmethod
    def _as_dict_with_list(data, key):
        """Like _as_list, but for endpoints that wrap the array in {key: [...]}"""
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
        return None

    # ------------------------------------------------------------------ #
    # Courses
    # ------------------------------------------------------------------ #
    def get_courses(self):
        data = self._execute_api(f'/d2l/api/lp/{self.LP_VERSION}/enrollments/myenrollments/')
        items = self._as_dict_with_list(data, 'Items')
        if items is None:
            print("API Response Error or Empty:", data)
            return []

        return [
            Course(item['OrgUnit']['Id'], item['OrgUnit']['Name'])
            for item in items
            if isinstance(item, dict)
            and item.get('OrgUnit', {}).get('Type', {}).get('Code') == 'Course Offering'
        ]

    def get_course_content(self, course_id):
        """
        Top-level content nodes for a course. NOTE: content/root/ only
        returns the top level — mostly Modules (folders). To get the actual
        Topics inside them, call .get_all_topics(session) on the Course, or
        .get_children(session)/.get_all_topics(session) on a CourseContent
        node directly.
        """
        data = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/content/root/')
        items = self._as_list(data)
        if items is None:
            print("API Response Error or Empty:", data)
            return []

        return [
            CourseContent(
                content_id=item.get('Id'),
                title=item.get('Title'),
                content_type=item.get('Type'),
                course_id=course_id,
                topic_type=item.get('TopicType'),
                mime_type=item.get('MimeType'),
                url=item.get('Url'),
            )
            for item in items
            if isinstance(item, dict)
        ]

    # ------------------------------------------------------------------ #
    # Assignments (Dropbox folders)
    # ------------------------------------------------------------------ #
    def get_assignments(self, course_id):
        data = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/dropbox/folders/')
        items = self._as_list(data)
        if items is None:
            print("API Response Error or Empty:", data)
            return []

        return [
            {
                "id": item.get('Id'),
                "name": item.get('Name'),
                "due_date": item.get('DueDate'),
                "total_points": item.get('TotalPoints'),
                "instructions": (item.get('CustomInstructions') or {}).get('Text'),
            }
            for item in items
            if isinstance(item, dict)
        ]

    # ------------------------------------------------------------------ #
    # Grades
    # ------------------------------------------------------------------ #
    def get_grades(self, course_id):
        items_raw = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/grades/')
        values_raw = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/grades/values/myGradeValues/')

        items = self._as_list(items_raw)
        values = self._as_list(values_raw)
        if items is None or values is None:
            print("API Response Error or Empty:", {"items": items_raw, "values": values_raw})
            return []

        item_names = {i.get('Id'): i.get('Name') for i in items if isinstance(i, dict)}

        return [
            {
                "grade_item_id": v.get('GradeObjectIdentifier'),
                "name": item_names.get(v.get('GradeObjectIdentifier'), 'Unknown'),
                "points": v.get('PointsNumerator'),
                "out_of": v.get('PointsDenominator'),
                "displayed_grade": v.get('DisplayedGrade'),
            }
            for v in values
            if isinstance(v, dict)
        ]

    # ------------------------------------------------------------------ #
    # Announcements
    # ------------------------------------------------------------------ #
    def get_announcements(self, course_id):
        data = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/news/')
        items = self._as_list(data)
        if items is None:
            print("API Response Error or Empty:", data)
            return []

        return [
            {
                "id": item.get('Id'),
                "title": item.get('Title'),
                "body": (item.get('Body') or {}).get('Text'),
                "start_date": item.get('StartDate'),
                "end_date": item.get('EndDate'),
            }
            for item in items
            if isinstance(item, dict)
        ]

    # ------------------------------------------------------------------ #
    # Quizzes
    # ------------------------------------------------------------------ #
    def get_quizzes(self, course_id):
        data = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/quizzes/')
        objects = self._as_dict_with_list(data, 'Objects')
        if objects is None:
            print("API Response Error or Empty:", data)
            return []

        return [
            {
                "id": q.get('QuizId'),
                "name": q.get('Name'),
                "start_date": q.get('StartDate'),
                "end_date": q.get('EndDate'),
            }
            for q in objects
            if isinstance(q, dict)
        ]


# Test the script
if __name__ == "__main__":
    session = Session()

    allCourses = session.get_courses()
    print("Courses:")
    for course in allCourses:
        print(f"- {course.name} (ID: {course.course_id})")


    for course in allCourses:
        courseId = course.course_id
        courseName = course.name
        assignments = session.get_assignments(courseId)

        print(f"Assignments for course {courseName} (ID: {courseId}):")
        for assignment in assignments:
            print(f"- {assignment['name']} (Due: {assignment['due_date']}, Points: {assignment['total_points']})")
