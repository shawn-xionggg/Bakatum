import json
import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


class Course:
    def __init__(self, course_id, name):
        self.course_id = course_id
        self.name = name

    def get_content(self, session):
        return session.get_course_content(self.course_id)

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

    def _download_binary(self, api_path, dest_path):
        """GET a binary endpoint (e.g. a content topic file) and save it to disk."""
        def run(context):
            resp = context.request.get(f"{self.BASE_URL}{api_path}")
            if not resp.ok:
                return {"error": resp.status, "statusText": resp.status_text}
            body = resp.body()
            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(body)
            return {"saved_to": dest_path, "bytes": len(body), "content_type": resp.headers.get("content-type")}

        return self._with_context(run)

    # ------------------------------------------------------------------ #
    # Courses
    # ------------------------------------------------------------------ #
    def get_courses(self):
        data = self._execute_api(f'/d2l/api/lp/{self.LP_VERSION}/enrollments/myenrollments/')
        if not data or 'Items' not in data:
            print("API Response Error or Empty:", data)
            return []

        # NOTE: this used to be wrapped in `{ ... }` (a set literal containing
        # one Course each), which is why str(course) never showed your
        # __str__ output — you were printing a 1-item set, not the Course.
        return [
            Course(item['OrgUnit']['Id'], item['OrgUnit']['Name'])
            for item in data['Items']
            if item.get('OrgUnit', {}).get('Type', {}).get('Code') == 'Course Offering'
        ]

    def get_course_content(self, course_id):
        data = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/content/root/')
        if not data:
            print("API Response Error or Empty:", data)
            return []

        return [
            {"id": item['Id'], "title": item.get('Title'), "type": item.get('Type')}
            for item in data
        ]

    # ------------------------------------------------------------------ #
    # Assignments (Dropbox folders)
    # ------------------------------------------------------------------ #
    def get_assignments(self, course_id):
        data = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/dropbox/folders/')
        if not data:
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
            for item in data
        ]

    # ------------------------------------------------------------------ #
    # Grades
    # ------------------------------------------------------------------ #
    def get_grades(self, course_id):
        items = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/grades/')
        values = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/grades/values/myGradeValues/')

        if not items or not values:
            print("API Response Error or Empty:", {"items": items, "values": values})
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
        if not data:
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
            for item in data
        ]

    # ------------------------------------------------------------------ #
    # Quizzes
    # ------------------------------------------------------------------ #
    def get_quizzes(self, course_id):
        data = self._execute_api(f'/d2l/api/le/{self.LE_VERSION}/{course_id}/quizzes/')
        if not data or 'Objects' not in data:
            print("API Response Error or Empty:", data)
            return []

        return [
            {
                "id": q.get('QuizId'),
                "name": q.get('Name'),
                "start_date": q.get('StartDate'),
                "end_date": q.get('EndDate'),
            }
            for q in data['Objects']
        ]


# Test the script
if __name__ == "__main__":
    session = Session()
    courses = session.courses

    print("Courses:")
    for course in courses:
        print(str(course))

    if courses:
        c = courses[0]
        print(f"\nAssignments for {c.name}:")
        for a in c.get_assignments(session):
            print(" ", a)