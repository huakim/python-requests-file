from requests.adapters import BaseAdapter
from requests.compat import urlparse, unquote
from urllib.parse import parse_qs
from requests import Response, codes
import errno
import os
import stat
import locale
import io

try:
    from io import BytesIO
except ImportError:
    from StringIO import StringIO as BytesIO


class FuncStr:
    def __init__(self, func):
        this.func = func
    def __str__(self):
        return this.func()


def setPath(resp, path):
    path = str(path) + resp.file_path
    resp.file_path = path
    resp.url_netloc = "localhost"


def readExceptionObject(resp, e, status_code=codes.internal_server_error):
    """Wraps an Exception object text in a Response object.

    :param resp: The Response` being "sent".
    :param e: The Exception object
    :returns: a Response object containing the file
    """
    # Wrap the error message in a file-like object
    # The error message will be localized, try to convert the string
    # representation of the exception into a byte stream
    resp_str = str(e).encode(locale.getpreferredencoding(False))

    resp.raw = BytesIO(resp_str)
    resp.reason = resp_str
    # set error object
    resp.error = e

    if resp._set_content_length:
        resp.headers["Content-Length"] = len(resp_str)

    # Add release_conn to the BytesIO object
    resp.raw.release_conn = resp.raw.close

    stat_code = False
    try:
        stat_code = not (resp.status_code is None)
    except AttributeError:
        pass

    if not stat_code:
        resp.status_code = status_code

    return resp


def readTextFile(resp, raw=None, length=None):
    """Wraps a file, described in request, in a Response object.

    :param resp: The Response` being "sent".
    :returns: a Response object containing the file text
    """
    # Use io.open since we need to add a release_conn method, and
    # methods can't be added to file objects in python 2.
    if raw is None:
        raw = io.open(resp.file_path, "rb")

    resp.raw = raw
    resp.raw.release_conn = resp.raw.close

    resp.status_code = codes.ok

    # If it's a regular file, set the Content-Length
    if resp._set_content_length:
        if length is None:
            resp_stat = os.fstat(resp.raw.fileno())
            if stat.S_ISREG(resp_stat.st_mode):
                length = resp_stat.st_size
        resp.headers["Content-Length"] = length

    return resp


class FileAdapter(BaseAdapter):
    def __init__(self, set_content_length=True, netloc_paths = {}):
        super(FileAdapter, self).__init__()
        self._handlers = []
        self._netlocs = {}
        self._set_content_length = set_content_length
        for key, value in netloc_paths.items():
            self.add_netloc(key, value)

    def add_handler(self, func):
        """Add custom handler for modify response on the fly

        :param func: The handler function being added.
        """
        if callable(func):
            self._handlers.append(func)

    def add_netloc(self, name, func):
        """Add custom netloc handler for monify response on the fly

        :param name: The netloc name
        :param func: The handler function being added
        """
        if callable(func):
            self._netlocs[name] = func
        else:
            self._netlocs[name] = lambda resp: setPath(resp, func)

    def send(self, request, **kwargs):
        """Wraps a file, described in request, in a Response object.

        :param request: The PreparedRequest` being "sent".
        :returns: a Response object containing the file
        """

        # Parse the URL
        url_parts = urlparse(request.url)

        url_netloc = url_parts.netloc

        resp = Response()
        resp.request = request
        resp.url = request.url
        resp.query_params = parse_qs(url_parts.query)
        resp._set_content_length = self._set_content_length

        # Open the file, translate certain errors into HTTP responses
        # Use urllib's unquote to translate percent escapes into whatever
        # they actually need to be
        try:
            # Split the path on / (the URL directory separator) and decode any
            # % escapes in the parts
            path_parts = [unquote(p) for p in url_parts.path.split("/")]

            # Strip out the leading empty parts created from the leading /'s
            while path_parts and not path_parts[0]:
                path_parts.pop(0)

            # If os.sep is in any of the parts, someone fed us some shenanigans.
            # Treat is like a missing file.
            if any(os.sep in p for p in path_parts):
                raise IOError(errno.ENOENT, os.strerror(errno.ENOENT))

            # Look for a drive component. If one is present, store it separately
            # so that a directory separator can correctly be added to the real
            # path, and remove any empty path parts between the drive and the path.
            # Assume that a part ending with : or | (legacy) is a drive.
            if path_parts and (
                path_parts[0].endswith("|") or path_parts[0].endswith(":")
            ):
                path_drive = path_parts.pop(0)
                if path_drive.endswith("|"):
                    path_drive = path_drive[:-1] + ":"

                while path_parts and not path_parts[0]:
                    path_parts.pop(0)
            else:
                path_drive = ""

            # Try to put the path back together
            # Join the drive back in, and stick os.sep in front of the path to
            # make it absolute.
            path = path_drive + os.sep + os.path.join(*path_parts)

            # Check if the drive assumptions above were correct. If path_drive
            # is set, and os.path.splitdrive does not return a drive, it wasn't
            # really a drive. Put the path together again treating path_drive
            # as a normal path component.
            if path_drive and not os.path.splitdrive(path):
                path = os.sep + os.path.join(path_drive, *path_parts)

            # Add file_path and url_netloc attributes for using with adapters
            resp.file_path = path
            resp.url_netloc = url_netloc or "localhost"
            resp.raw = None

            func = self._netlocs.get(resp.url_netloc)
            if callable(func):
                func(resp)

            for func in self._handlers:
                func(resp)

            if resp.raw is None:
                method = request.method
                url_netloc = resp.url_netloc
                # Check that the method makes sense. Only support GET
                if method not in ("GET", "HEAD"):
                    resp.status_code = codes.method_not_allowed
                    raise ValueError("Invalid request method %s" % method)
                # Reject URLs with a hostname component
                if url_netloc != "localhost":
                    resp.status_code = codes.forbidden
                    raise ValueError(
                        "file: URLs with hostname components are not permitted"
                    )
                resp = readTextFile(resp)
            return resp
        except IOError as e:
            if e.errno == errno.EACCES:
                status_code = codes.forbidden
            elif e.errno == errno.ENOENT:
                status_code = codes.not_found
            else:
                status_code = codes.bad_request
            # Wrap the error message in a file-like object
            # The error message will be localized, try to convert the string
            # representation of the exception into a byte stream
            return readExceptionObject(resp, e, status_code)
        except Exception as e:
            return readExceptionObject(resp, e)

    def close(self):
        pass
