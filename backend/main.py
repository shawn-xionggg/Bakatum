"""Authenticated syllabus upload API. Run from backend/: uvicorn main:app --reload."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import logging
import os
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pypdf import PdfReader
from supabase import create_client

load_dotenv(Path(__file__).with_name('.env'))
MAX_BYTES = 20 * 1024 * 1024
BUCKET = 'syllabuses'
logger = logging.getLogger(__name__)
app = FastAPI(title='Grape Study API', version='0.1.0')
app.add_middleware(CORSMiddleware,
    allow_origins=[os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000')],
    allow_credentials=False, allow_methods=['GET', 'POST'],
    allow_headers=['Authorization', 'Content-Type'])
bearer = HTTPBearer(auto_error=False)


class UploadBodyLimit:
    """Enforce a streaming body limit before multipart parsing writes large files."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        total = 0
        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get('body', b''))
            if total > MAX_BYTES + 1024 * 1024:
                raise HTTPException(413, 'Upload is too large. Maximum PDF size is 20 MB.')
            return message
        # Content-Length catches normal browser requests before parsing begins.
        headers = dict(scope['headers'])
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length.'}, 400)(scope, receive, send)
        if declared > MAX_BYTES + 1024 * 1024:
            return await JSONResponse({'detail': 'Maximum PDF size is 20 MB.'}, 413)(scope, receive, send)
        return await self.app(scope, bounded_receive, send)

app.add_middleware(UploadBodyLimit)


@lru_cache
def database():
    url, key = os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SECRET_KEY')
    if not url or not key or 'your-project' in url or key == 'your-server-secret-key':
        raise HTTPException(503, 'Supabase is not configured. Complete backend/.env and run supabase/setup.sql.')
    return create_client(url, key)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, 'Sign in before uploading.')
    db = database()
    try:
        user = db.auth.get_user(credentials.credentials).user
    except Exception:
        raise HTTPException(401, 'Your session could not be verified. Sign in again.')
    if not user:
        raise HTTPException(401, 'Sign in again.')
    return user.id


def validate_pdf(filename: str, data: bytes) -> int:
    if not filename.lower().endswith('.pdf'):
        raise HTTPException(415, 'Choose a PDF file.')
    if not data:
        raise HTTPException(400, 'This file is empty.')
    if len(data) > MAX_BYTES:
        raise HTTPException(413, 'Maximum PDF size is 20 MB.')
    if not data.startswith(b'%PDF-'):
        raise HTTPException(415, 'This file is not a valid PDF.')
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise HTTPException(422, 'Remove the PDF password before uploading.')
        pages = len(reader.pages)
        if pages < 1:
            raise HTTPException(422, 'The PDF has no pages.')
        return pages
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'This PDF could not be read. Export it as a new PDF and retry.')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/syllabuses')
def list_syllabuses(user_id: str = Depends(current_user)):
    try:
        result = database().table('syllabuses').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
        return {'syllabuses': result.data}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Could not load syllabuses. Check the Supabase connection and SQL setup.')


@app.post('/syllabuses/upload', status_code=201)
def upload_syllabus(file: UploadFile = File(...), user_id: str = Depends(current_user)):
    # The browser queues any number of files and sends one per request. Failures
    # can be retried individually without re-uploading successful files.
    try:
        data = file.file.read(MAX_BYTES + 1)
        filename = (file.filename or 'syllabus.pdf').replace('\\', '/').split('/')[-1][:240]
        pages = validate_pdf(filename, data)
        file_id = str(uuid4())
        path = f'{user_id}/{file_id}.pdf'
        row = {'id': file_id, 'user_id': user_id, 'filename': filename,
               'storage_path': path, 'size_bytes': len(data), 'page_count': pages, 'status': 'uploaded'}
        db = database()
        try:
            db.storage.from_(BUCKET).upload(path, data, {'content-type': 'application/pdf', 'upsert': 'false'})
        except Exception:
            raise HTTPException(503, 'File storage failed. Check the private syllabuses bucket and retry.')
        try:
            result = db.table('syllabuses').insert(row).execute()
            return {'syllabus': result.data[0]}
        except Exception:
            # Compensate for database failure after storage upload.
            try:
                db.storage.from_(BUCKET).remove([path])
            except Exception:
                logger.error('Storage cleanup failed for syllabus id %s; manual cleanup needed.', file_id)
            raise HTTPException(503, 'File record could not be saved. Check the SQL setup and retry.')
    finally:
        file.file.close()


@app.get('/syllabuses/{syllabus_id}/url')
def syllabus_url(syllabus_id: str, user_id: str = Depends(current_user)):
    db = database()
    try:
        rows = db.table('syllabuses').select('storage_path').eq('id', syllabus_id).eq('user_id', user_id).execute().data
        if not rows:
            raise HTTPException(404, 'Syllabus not found.')
        result = db.storage.from_(BUCKET).create_signed_url(rows[0]['storage_path'], 60)
        return {'url': result['signedURL']}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Could not open this PDF. Try again.')