```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { createClient, type Session } from "@supabase/supabase-js";
import "./stylesheet.css";

type QueuedFile = {
  id: string;
  file: File;
  status: "queued" | "uploading" | "uploaded" | "error";
  error?: string;
};

type Syllabus = {
  id: string;
  filename: string;
  size_bytes: number;
  page_count: number;
  created_at: string;
  status: string;
};

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

const configured = Boolean(
  url &&
  key &&
  !url.includes("your-project") &&
  key !== "your-publishable-key"
);

const supabase = configured
  ? createClient(url!, key!)
  : null;

const api =
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000/";

const MAX = 20 * 1024 * 1024;

const size = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.ceil(bytes / 1024)} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;


export default function Home() {

  const [session, setSession] =
    useState<Session | null>(null);

  const [queue, setQueue] =
    useState<QueuedFile[]>([]);

  const [saved, setSaved] =
    useState<Syllabus[]>([]);

  const [message, setMessage] =
    useState("");

  const [authMessage, setAuthMessage] =
    useState("");

  const [email, setEmail] =
    useState("");

  const [password, setPassword] =
    useState("");

  const [mode, setMode] =
    useState<"signin" | "signup">("signin");

  const [busy, setBusy] =
    useState(false);

  const [authBusy, setAuthBusy] =
    useState(false);

  const [loading, setLoading] =
    useState(false);

  const [dragging, setDragging] =
    useState(false);

  const picker =
    useRef<HTMLInputElement>(null);

  const pending =
    queue.filter(
      item =>
        item.status === "queued" ||
        item.status === "error"
    ).length;


  /* =========================================
     SUPABASE SESSION
     ========================================= */

  useEffect(() => {

    if (!supabase) return;

    supabase.auth
      .getSession()
      .then(({ data }) =>
        setSession(data.session)
      );

    const { data } =
      supabase.auth.onAuthStateChange(
        (_event, next) =>
          setSession(next)
      );

    return () =>
      data.subscription.unsubscribe();

  }, []);


  /* =========================================
     API REQUEST
     ========================================= */

  async function request(
    path: string,
    options: RequestInit = {}
  ) {

    const { data } =
      await supabase!.auth.getSession();

    if (!data.session)
      throw new Error(
        "Sign in before uploading."
      );

    const response = await fetch(
      `${api}${path}`,
      {
        ...options,

        headers: {
          ...options.headers,
          Authorization:
            `Bearer ${data.session.access_token}`,
        },

        signal:
          AbortSignal.timeout(120000),
      }
    );

    const body =
      await response.json();

    if (!response.ok) {

      throw new Error(
        typeof body.detail === "string"
          ? body.detail
          : "Request failed. Try again."
      );

    }

    return body;
  }


  /* =========================================
     LOAD SAVED SYLLABUSES
     ========================================= */

  useEffect(() => {

    let cancelled = false;

    setSaved([]);

    if (!session?.user.id)
      return;

    setLoading(true);

    request("/syllabuses")

      .then(data => {

        if (!cancelled)
          setSaved(data.syllabuses);

      })

      .catch(error => {

        if (!cancelled) {

          setMessage(
            error instanceof TypeError
              ? "Cannot reach the backend. Start FastAPI on port 8000."
              : error.message
          );

        }

      })

      .finally(() => {

        if (!cancelled)
          setLoading(false);

      });

    return () => {
      cancelled = true;
    };

  }, [session?.user.id]);


  /* =========================================
     ADD FILES
     ========================================= */

  function addFiles(
    files: FileList | File[]
  ) {

    if (busy)
      return;

    setMessage("");

    const valid: File[] = [];
    const errors: string[] = [];

    Array.from(files).forEach(file => {

      if (
        !file.name
          .toLowerCase()
          .endsWith(".pdf")
      ) {

        errors.push(
          `${file.name}: choose a PDF.`
        );

      } else if (!file.size) {

        errors.push(
          `${file.name}: file is empty.`
        );

      } else if (file.size > MAX) {

        errors.push(
          `${file.name}: exceeds 20 MB.`
        );

      } else {

        valid.push(file);

      }

    });


    setQueue(previous => {

      const next = [...previous];

      valid.forEach(file => {

        if (
          !next.some(
            item =>
              item.file.name === file.name &&
              item.file.size === file.size &&
              item.file.lastModified ===
                file.lastModified
          )
        ) {

          next.push({
            id: crypto.randomUUID(),
            file,
            status: "queued",
          });

        }

      });

      return next;

    });


    if (errors.length)
      setMessage(errors.join(" "));

  }


  /* =========================================
     AUTHENTICATION
     ========================================= */

  async function authenticate(
    event: React.FormEvent
  ) {

    event.preventDefault();

    if (!supabase)
      return;

    setAuthBusy(true);
    setAuthMessage("");

    try {

      const result =
        mode === "signin"

          ? await supabase.auth.signInWithPassword({
              email,
              password,
            })

          : await supabase.auth.signUp({
              email,
              password,
              options: {
                emailRedirectTo:
                  window.location.origin,
              },
            });


      if (result.error)
        throw result.error;


      if (
        mode === "signup" &&
        !result.data.session
      ) {

        setAuthMessage(
          "Check your email to confirm your account, then sign in."
        );

      }

      setPassword("");

    } catch (error) {

      setAuthMessage(
        error instanceof Error
          ? error.message
          : "Sign-in failed."
      );

    } finally {

      setAuthBusy(false);

    }

  }


  /* =========================================
     UPLOAD
     ========================================= */

  async function upload() {

    if (!session || busy)
      return;

    setBusy(true);
    setMessage("");

    for (
      const item of queue.filter(
        item =>
          item.status === "queued" ||
          item.status === "error"
      )
    ) {

      setQueue(previous =>
        previous.map(q =>
          q.id === item.id
            ? {
                ...q,
                status: "uploading",
                error: undefined,
              }
            : q
        )
      );


      try {

        const form =
          new FormData();

        form.append(
          "file",
          item.file
        );


        const data =
          await request(
            "/syllabuses/upload",
            {
              method: "POST",
              body: form,
            }
          );


        setSaved(previous => [
          data.syllabus,
          ...previous,
        ]);


        setQueue(previous =>
          previous.map(q =>
            q.id === item.id
              ? {
                  ...q,
                  status: "uploaded",
                }
              : q
          )
        );


      } catch (error) {

        const detail =
          error instanceof TypeError
            ? "Cannot reach the backend. Check that FastAPI is running."
            : error instanceof Error
              ? error.message
              : "Upload failed.";


        setQueue(previous =>
          previous.map(q =>
            q.id === item.id
              ? {
                  ...q,
                  status: "error",
                  error: detail,
                }
              : q
          )
        );

      }

    }

    setBusy(false);

  }


  /* =========================================
     OPEN PDF
     ========================================= */

  async function openPdf(id: string) {

    const tab =
      window.open(
        "about:blank",
        "_blank"
      );

    if (tab)
      tab.opener = null;

    try {

      const data =
        await request(
          `/syllabuses/${id}/url`
        );

      if (tab)
        tab.location.href =
          data.url;

      else
        setMessage(
          "Allow popups for this page to open the PDF."
        );

    } catch (error) {

      tab?.close();

      setMessage(
        error instanceof Error
          ? error.message
          : "Could not open PDF."
      );

    }

  }


  /* =========================================
     PAGE
     ========================================= */

  return (

    <div className="shell">

      {/* =====================================
          NAVIGATION BAR
          ===================================== */}

      <header className="navigationBar">

        <div className="logo">

          <a href="/">

            <img
              src="/navlogo.png"
              alt="Bakatum"
            />

          </a>

        </div>


        <div className="navigationLinks">

          {session ? (

            <button
              className="text-button account"
              disabled={busy}
              onClick={async () => {

                const result =
                  await supabase!.auth.signOut();

                if (result.error) {

                  setMessage(
                    result.error.message
                  );

                  return;

                }

                setQueue([]);
                setSaved([]);

              }}
            >
              Sign out
            </button>

          ) : (

            <a href="#login">
              Log In
            </a>

          )}

        </div>

      </header>


      {/* =====================================
          MAIN
          ===================================== */}

      <main>

        {/* ===================================
            HERO WITH ANIMATED GRAPES
            =================================== */}

        <section className="hero">

          {/* Animated grapes */}

          <div
            className="floatingGrapes"
            aria-hidden="true"
          >

            <img
              src="/grape.png"
              className="grape grape1"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape2"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape3"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape4"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape5"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape6"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape7"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape8"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape9"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape10"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape11"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape12"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape13"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape14"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape15"
              alt=""
            />

            <img
              src="/grape.png"
              className="grape grape16"
              alt=""
            />

          </div>


          {/* Hero content */}

          <div className="heroContent">

            <div className="heroText">

              <p className="heroTag">
                FOCUS. STUDY. ACHIEVE.
              </p>

              <h1>
                Make your study time{" "}
                <span>count.</span>
              </h1>

              <p className="heroDescription">
                Bakatum helps you stay focused,
                manage your study sessions,
                and build better study habits
                one session at a time.
              </p>

              <div className="heroButtons">

                <a
                  href="#upload-title"
                  className="startButton"
                >
                  Start Studying{" "}
                  <i className="fa-solid fa-arrow-right"></i>
                </a>

                <a
                  href="#features"
                  className="learnButton"
                >
                  Learn More
                </a>

              </div>

            </div>


            {/* Timer preview */}

            <div className="heroVisual">

              <div className="studyCard">

                <div className="cardHeader">

                  <div>

                    <p className="cardLabel">
                      CURRENT SESSION
                    </p>

                    <h3>
                      Deep Focus
                    </h3>

                  </div>

                  <div className="statusCircle">

                    <i className="fa-solid fa-brain"></i>

                  </div>

                </div>


                <div className="timerDisplay">
                  25:00
                </div>

                <p className="timerText">
                  Ready when you are.
                </p>


                <div className="progressBar">

                  <div className="progress"></div>

                </div>


                <div className="cardFooter">

                  <span>

                    <i className="fa-regular fa-clock"></i>

                    25 minutes

                  </span>


                  <span>

                    <i className="fa-solid fa-bolt"></i>

                    Focus mode

                  </span>

                </div>

              </div>


              <div className="floatingCard">

                <i className="fa-solid fa-check"></i>

                <div>

                  <strong>
                    Stay consistent
                  </strong>

                  <p>
                    One session at a time.
                  </p>

                </div>

              </div>

            </div>

          </div>

        </section>


        {/* ===================================
            EXISTING SYLLABUS WORKSPACE
            =================================== */}

        <div className="intro">

          <span className="eyebrow">
            YOUR SEMESTER STARTS HERE
          </span>

          <h1>
            Bring your courses
            <br />
            into one place.
          </h1>

          <p>
            Add your syllabuses.
            One course or seven—
            there’s room for all of them.
          </p>

        </div>


        <div className="layout">

          <section
            className="upload-panel"
            aria-labelledby="upload-title"
          >

            <div className="section-heading">

              <div>

                <span className="step">
                  01 / IMPORT
                </span>

                <h2 id="upload-title">
                  Upload syllabuses
                </h2>

              </div>

              <span className="tag">
                PDF files
              </span>

            </div>


            <input
              ref={picker}
              className="sr-only"
              type="file"
              accept=".pdf,application/pdf"
              multiple
              aria-label="Select syllabus PDFs"
              disabled={busy}
              onChange={event => {

                if (event.target.files)
                  addFiles(
                    event.target.files
                  );

                event.target.value = "";

              }}
            />


            <button
              className={`dropzone ${
                dragging
                  ? "dragging"
                  : ""
              }`}
              disabled={busy}

              onClick={() =>
                picker.current?.click()
              }

              onDragOver={event => {

                event.preventDefault();
                setDragging(true);

              }}

              onDragLeave={() =>
                setDragging(false)
              }

              onDrop={event => {

                event.preventDefault();

                setDragging(false);

                addFiles(
                  event.dataTransfer.files
                );

              }}
            >

              <span
                className="upload-symbol"
                aria-hidden="true"
              >
                ↑
              </span>

              <strong>
                Drop your PDFs here
              </strong>

              <span>
                or <u>browse files</u>
              </span>

              <small>
                Multiple files · Up to 20 MB each
              </small>

            </button>


            {message && (

              <p
                className="notice error"
                role="alert"
              >
                {message}
              </p>

            )}


            {queue.length > 0 && (

              <div
                className="queue"
                aria-live="polite"
              >

                {queue.map(item => (

                  <div
                    className="file-row"
                    key={item.id}
                  >

                    <span
                      className="pdf-icon"
                      aria-hidden="true"
                    >
                      PDF
                    </span>

                    <div className="file-info">

                      <strong>
                        {item.file.name}
                      </strong>

                      <span>

                        {size(
                          item.file.size
                        )}

                        {" · "}

                        {item.status ===
                        "queued"
                          ? "Ready to upload"
                          : item.status ===
                            "uploading"
                            ? "Uploading…"
                            : item.status ===
                              "uploaded"
                              ? "Saved"
                              : "Upload failed"}

                      </span>


                      {item.error && (

                        <p className="file-error">
                          {item.error}
                        </p>

                      )}

                    </div>


                    {item.status !==
                      "uploading" && (

                      <button
                        className="remove"
                        disabled={busy}
                        aria-label={
                          `Remove ${item.file.name} from queue`
                        }

                        onClick={() =>
                          setQueue(
                            previous =>
                              previous.filter(
                                q =>
                                  q.id !==
                                  item.id
                              )
                          )
                        }
                      >
                        ×
                      </button>

                    )}

                  </div>

                ))}

              </div>

            )}


            <div className="upload-footer">

              <span>

                {pending
                  ? `${pending} file${
                      pending === 1
                        ? ""
                        : "s"
                    } ready`
                  : "No files waiting"}

              </span>


              <button
                className="primary"
                disabled={
                  !session ||
                  !pending ||
                  busy
                }

                onClick={upload}
              >

                {busy
                  ? "Uploading…"
                  : `Upload${
                      pending
                        ? ` ${pending} PDF${
                            pending === 1
                              ? ""
                              : "s"
                          }`
                        : " PDFs"
                    }`}

              </button>

            </div>


            <p className="privacy">
              Your PDFs stay private.
              Uploading does not send
              them to AI.
            </p>

          </section>


          {/* =================================
              SIDEBAR
              ================================= */}

          <aside id="login">

            {!configured ? (

              <section className="side-card">

                <span className="step">
                  CONNECT YOUR WORKSPACE
                </span>

                <h2>
                  Setup required
                </h2>

                <p>
                  Add your Supabase settings
                  to start saving syllabuses.
                </p>

                <p>
                  You can select files now.
                  Follow the README included
                  with this project to enable
                  uploads.
                </p>

              </section>

            ) : !session ? (

              <section className="side-card">

                <span className="step">
                  YOUR PRIVATE WORKSPACE
                </span>

                <h2>
                  {mode === "signin"
                    ? "Sign in to save"
                    : "Create an account"}
                </h2>

                <p>
                  Select your files, then
                  sign in to upload them.
                </p>


                <form
                  onSubmit={authenticate}
                >

                  <label>

                    Email

                    <input
                      type="email"
                      autoComplete="email"
                      required
                      value={email}

                      onChange={event =>
                        setEmail(
                          event.target.value
                        )
                      }
                    />

                  </label>


                  <label>

                    Password

                    <input
                      type="password"
                      autoComplete={
                        mode === "signin"
                          ? "current-password"
                          : "new-password"
                      }
                      minLength={6}
                      required
                      value={password}

                      onChange={event =>
                        setPassword(
                          event.target.value
                        )
                      }
                    />

                  </label>


                  <button
                    className="primary full"
                    disabled={authBusy}
                  >

                    {authBusy
                      ? "Please wait…"
                      : mode === "signin"
                        ? "Sign in"
                        : "Create account"}

                  </button>

                </form>


                {authMessage && (

                  <p
                    role="status"
                    className="notice"
                  >
                    {authMessage}
                  </p>

                )}


                <button
                  className="text-button"
                  disabled={authBusy}

                  onClick={() => {

                    setMode(
                      mode === "signin"
                        ? "signup"
                        : "signin"
                    );

                    setAuthMessage("");

                  }}
                >

                  {mode === "signin"
                    ? "Create an account"
                    : "Already have an account? Sign in"}

                </button>

              </section>

            ) : (

              <section className="side-card">

                <span className="step">
                  YOUR WORKSPACE
                </span>

                <div className="big-count">
                  {saved.length
                    .toString()
                    .padStart(2, "0")}
                </div>

                <h2>
                  Syllabuses saved
                </h2>

                <p className="user-email">
                  {session.user.email}
                </p>

                <p>
                  Upload more whenever
                  you need. Your files will
                  be here when you return.
                </p>

              </section>

            )}


            <div className="next-note">

              <span className="step">
                UP NEXT
              </span>

              <h3>
                From syllabus to study plan.
              </h3>

              <p>
                Task extraction and deadline
                review will be added in the
                next build.
              </p>

            </div>

          </aside>

        </div>


        {/* ===================================
            SAVED SYLLABUSES
            =================================== */}

        <section
          className="saved-section"
          aria-labelledby="saved-title"
        >

          <div className="saved-heading">

            <h2 id="saved-title">
              Your syllabuses
            </h2>

            <span>
              {saved.length} saved
            </span>

          </div>


          {loading ? (

            <p role="status">
              Loading syllabuses…
            </p>

          ) : saved.length ? (

            <div className="saved-list">

              {saved.map(item => (

                <div
                  className="file-row"
                  key={item.id}
                >

                  <span
                    className="pdf-icon"
                    aria-hidden="true"
                  >
                    PDF
                  </span>

                  <div className="file-info">

                    <strong>
                      {item.filename}
                    </strong>

                    <span>

                      {item.page_count}
                      {" page"}
                      {item.page_count === 1
                        ? ""
                        : "s"}

                      {" · "}

                      {size(
                        item.size_bytes
                      )}

                      {" · "}

                      {new Date(
                        item.created_at
                      ).toLocaleDateString()}

                    </span>

                  </div>


                  <button
                    className="text-button"
                    onClick={() =>
                      openPdf(item.id)
                    }
                  >
                    Open PDF
                  </button>

                </div>

              ))}

            </div>

          ) : (

            <div className="empty">

              <span className="empty-line" />

              <p>

                No syllabuses yet.

                <br />

                <span>
                  Your uploaded files
                  will appear here.
                </span>

              </p>

            </div>

          )}

        </section>


        {/* ===================================
            FEATURES
            =================================== */}

        <section
          className="features"
          id="features"
        >

          <div className="sectionHeading">

            <p className="sectionTag">
              WHY BAKATUM?
            </p>

            <h2>
              A simpler way to{" "}
              <span>
                stay focused.
              </span>
            </h2>

            <p>
              Everything you need to make
              your study sessions more
              intentional and productive.
            </p>

          </div>


          <div className="featureGrid">

            <div className="featureCard">

              <div className="featureIcon">
                <i className="fa-solid fa-clock"></i>
              </div>

              <h3>
                Set Your Time
              </h3>

              <p>
                Choose how long you want
                to study and let Bakatum
                keep track of your session.
              </p>

            </div>


            <div className="featureCard">

              <div className="featureIcon">
                <i className="fa-solid fa-bullseye"></i>
              </div>

              <h3>
                Stay Focused
              </h3>

              <p>
                Create dedicated periods
                of uninterrupted focus so
                you can get more done.
              </p>

            </div>


            <div className="featureCard">

              <div className="featureIcon">
                <i className="fa-solid fa-chart-line"></i>
              </div>

              <h3>
                Build Better Habits
              </h3>

              <p>
                Turn consistent study
                sessions into habits that
                help you reach your goals.
              </p>

            </div>

          </div>

        </section>


        {/* ===================================
            BOTTOM CTA
            =================================== */}

        <section className="bottomCTA">

          <div>

            <p className="sectionTag">
              READY TO FOCUS?
            </p>

            <h2>
              Your next study session
              starts here.
            </h2>

            <p>
              Set your timer, eliminate
              distractions, and get started.
            </p>

          </div>


          <a
            href="#upload-title"
            className="ctaButton"
          >
            Start Studying{" "}
            <i className="fa-solid fa-arrow-right"></i>
          </a>

        </section>

      </main>


      {/* =====================================
          FOOTER
          ===================================== */}

      <footer>

        <span>
          BAKATUM / ONE COURSE AT A TIME
        </span>

        <p className="brand-story">
          Our name came from an AI mistake.
          Our reminder to verify what AI tells us.
        </p>

      </footer>
    </div>

  );
}