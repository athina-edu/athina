import requests
import time
import json


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
        except (json.decoder.JSONDecodeError, AttributeError):
            return_value = {}
    elif return_type == "text":
        try:
            return_value = data.text
        except:
            return_value = ""
    else:
        return_value = None

    return return_value
