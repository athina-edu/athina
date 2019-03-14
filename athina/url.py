import requests
import time
import json


def request_url(url, headers={}, payload={}, method="get", return_type="json", files={}):
    time.sleep(1)  # artificial delay in case code attempts to spam with requests
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
            return r.json()
        except json.decoder.JSONDecodeError:
            return {}
    elif return_type == "text":
        return r.text
    else:
        return None
