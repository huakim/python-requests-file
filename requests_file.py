from requests.adapters import BaseAdapter
from requests.compat import urlparse, unquote
from requests import Response, codes
import errno
import os
import stat
import locale
from pathlib import Path
from io import BytesIO
from urllib.parse import parse_qs


class FileAdapter(BaseAdapter):
    def __init__(self, set_content_length=True, netloc_paths={'localhost': ''}):
        super(FileAdapter, self).__init__()
        self.netlocs = dict(netloc_paths)
        self._set_content_length = set_content_length

    def open_raw(self, path, query):
        """Open a file as raw stream"""
        raw = Path(path).open('rb')
        # If it's a regular file, set the Content-Length
        resp_stat = os.fstat(raw.fileno())
        if stat.S_ISREG(resp_stat.st_mode):
            raw.len = resp_stat.st_size
        return raw

    def send(self, request, **kwargs):
        """Wraps a file, described in request, in a Response object.

        :param request: The PreparedRequest` being "sent".
        :returns: a Response object containing the file
        """

        # Check that the method makes sense. Only support GET
        if request.method not in ("GET", "HEAD"):
            raise ValueError("Invalid request method %s" % request.method)

        # Parse the URL
        url_parts = urlparse(request.url)

        # Get base path
        path = Path('/')

        # get url netloc
        netloc = url_parts.netloc
        if netloc:
            if not netloc in self.netlocs:
                raise ValueError("Domain " + netloc + " not mounted")
            else:
                path = path / Path(str(self.netlocs.get(str(netloc))))
        resp = Response()
        resp.request = request

        try:
            # Split the path on / (the URL directory separator) and decode any
            # % escapes in the parts
            path_parts = [unquote(p) for p in url_parts.path.split("/")]
            # If os.sep is in any of the parts, someone fed us some shenanigans.
            # Treat is like a missing file.
            for p in path_parts:
                if os.sep in p:
                    raise IOError(errno.ENOENT, os.strerror(errno.ENOENT))
                path = path / Path(p)
            # Use io.open since we need to add a release_conn method, and
            # methods can't be added to file objects in python 2.
            resp.raw = self.open_raw(path, parse_qs(url_parts.query))
            resp.raw.release_conn = resp.raw.close
        except IOError as e:
            if e.errno == errno.EACCES:
                resp.status_code = codes.forbidden
            elif e.errno == errno.ENOENT:
                resp.status_code = codes.not_found
            else:
                resp.status_code = codes.bad_request

            # Wrap the error message in a file-like object
            # The error message will be localized, try to convert the string
            # representation of the exception into a byte stream
            resp_str = str(e).encode(locale.getpreferredencoding(False))
            resp.raw = BytesIO(resp_str)
            if self._set_content_length:
                resp.headers["Content-Length"] = len(resp_str)

            # Add release_conn to the BytesIO object
            resp.raw.release_conn = resp.raw.close
        else:
            resp.status_code = codes.ok
            resp.url = request.url

            # If it's a regular file, set the Content-Length
            resp_stat = os.fstat(resp.raw.fileno())
            if self._set_content_length and hasattr(resp.raw, 'len'):
                resp.headers["Content-Length"] = resp.raw.len

        return resp

    def close(self):
        pass
