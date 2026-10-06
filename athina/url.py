import json
import time

import requests

__all__ = ('request_url',)


def request_url(url, headers=None, payload=None, method="get", return_type="json", files=None):
    time.sleep(1)  # artificial delay in case code attempts to spam with requests

    headers = {} if headers is None else headers
    payload = {} if payload is None else payload
    files = {} if files is None else files

    try:
        if method == "get":
            r = requests.get(url, headers=headers)
        elif method == "put":
            r = requests.put(url, headers=headers, data=payload)
        elif method == "post" and len(files) == 0:
            r = requests.post(url, headers=headers, data=payload)
        elif method == "post" and len(files) > 0:
            r = requests.post(url, headers=headers, data=payload, files=files)
        else:
            return None
    except (requests.exceptions.ConnectionError, requests.exceptions.MissingSchema):
        r = ""

    return _return_requested_type(r, return_type)


def _return_requested_type(data, return_type):
    if return_type == "json":
        try:
            return_value = data.json()
        except ValueError:
            # ValueError covers requests.exceptions.JSONDecodeError, which does
            # NOT inherit from json.JSONDecodeError (it descends from simplejson's
            # and from ValueError). Catching only json.JSONDecodeError let a
            # non-JSON reply — an HTML error page, a 401 from a bad token, a
            # connection failure — escape and crash the whole run instead of
            # being handled by the caller.
            return_value = {}
        except AttributeError:
            # data is not a Response at all (request_url returns "" when the
            # request could not be made).
            return_value = {}
    elif return_type == "text":
        try:
            return_value = data.text
        except Exception:
            return_value = ""
        except (SystemExit, KeyboardInterrupt):
            pass
    else:
        return_value = None

    return return_value
