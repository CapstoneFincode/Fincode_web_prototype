export function GET(request: Request) { return Response.redirect(new URL('/index.html', request.url), 302); }
