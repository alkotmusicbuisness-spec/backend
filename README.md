# StreamGrab backend — Railway

## Deploy
1. Create a Railway project and deploy this folder as a service (Dockerfile deployment is recommended).
2. Generate a public domain for the service.
3. Set `CORS_ORIGINS` to your Netlify site URL (or temporarily `*` while testing).
4. Put the Railway URL into the frontend `app.js` as `API_BASE`.

## Endpoints
- `GET /` health check
- `GET /api/formats?url=...` analyzes the URL and returns dynamically available video/audio formats.
- `GET /api/download?url=...&format_id=...&type=mp4|mp3` creates the requested file and streams it as a response.

## Important
This backend uses yt-dlp and FFmpeg. Availability and compatibility of third-party platforms can change. Use it only for content you are authorized to download. For a public service, add rate limiting, authentication, download-size/time limits, abuse controls, and cleanup of temporary files before exposing it broadly.
