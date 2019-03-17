import requests
import time
import json


def request_url(url, headers=None, payload=None, method="get", return_type="json", files=None):
    time.sleep(1)  # artificial delay in case code attempts to spam with requests

    return_value = None
    headers = {} if headers is None else headers
    payload = {} if payload is None else payload
    files = {} if files is None else files

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

    if return_type == "json":
        try:
            return_value = r.json()
        except json.decoder.JSONDecodeError:
            return_value = {}
    elif return_type == "text":
        return_value = r.text

    return return_value
