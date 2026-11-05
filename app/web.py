"""
Request and response values passed between the HTTP handler and the screens.

Screens never touch the socket. A route receives a Req and returns a Resp;
server.Handler turns the Resp into bytes on the wire.
"""

from collections import namedtuple

HTML = "text/html; charset=utf-8"

# inject_csrf: True for rendered pages - every <form> gets the session's CSRF
# field added on the way out, so no screen has to remember one.
Resp = namedtuple("Resp", "status body ctype extra cookie location inject_csrf")


def html(body, status=200, cookie=None):
    """A rendered page."""
    return Resp(status, body, HTML, None, cookie, None, True)


def send(body, status=200, ctype=HTML, extra=None, cookie=None):
    """Text or bytes sent exactly as given."""
    return Resp(status, body, ctype, extra, cookie, None, False)


def redirect(location, cookie=None):
    """303 See Other - the answer to every successful form post."""
    return Resp(303, b"", None, None, cookie, location, False)


def download(body, ctype, filename):
    """A file the browser should save rather than show."""
    return send(body, ctype=ctype,
                extra={"Content-Disposition": f'attachment; filename="{filename}"'})


class Req:
    """One parsed request: the client address, path, query and (for POST) form."""

    def __init__(self, ip, path, q, form=None, fields=None, files=None, cookie_token=""):
        self.ip = ip
        self.path = path
        self.q = q
        self.form = form or {}
        self.fields = fields or {}
        self.files = files or {}
        self.cookie_token = cookie_token

    def one(self, key, default=None):
        """The first value submitted for a form field."""
        return self.form.get(key, [default])[0]
