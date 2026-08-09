# Tests for athina.url (HTTP request helper).
import json
import unittest
from unittest import mock

import requests

from athina.url import request_url, _return_requested_type
from tests.helpers import make_config


class TestUrl(unittest.TestCase):
    def test_request_url_get_json(self):
        with mock.patch('athina.url.time.sleep'), \
                mock.patch('athina.url.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {"ok": True}
            result = request_url("http://example.com", method="get")
            self.assertEqual(result, {"ok": True})
            mock_get.assert_called_once_with("http://example.com", headers={})

    def test_request_url_put(self):
        with mock.patch('athina.url.time.sleep'), \
                mock.patch('athina.url.requests.put') as mock_put:
            mock_put.return_value.json.return_value = {"ok": True}
            result = request_url("http://example.com", payload={"a": 1}, method="put")
            self.assertEqual(result, {"ok": True})
            mock_put.assert_called_once_with("http://example.com", headers={}, data={"a": 1})

    def test_request_url_post_no_files(self):
        with mock.patch('athina.url.time.sleep'), \
                mock.patch('athina.url.requests.post') as mock_post:
            mock_post.return_value.json.return_value = {"ok": True}
            result = request_url("http://example.com", payload={"a": 1}, method="post")
            self.assertEqual(result, {"ok": True})
            mock_post.assert_called_once_with("http://example.com", headers={}, data={"a": 1})

    def test_request_url_post_with_files(self):
        with mock.patch('athina.url.time.sleep'), \
                mock.patch('athina.url.requests.post') as mock_post:
            mock_post.return_value.json.return_value = {"ok": True}
            result = request_url("http://example.com", payload={"a": 1}, files={"f": b"x"}, method="post")
            self.assertEqual(result, {"ok": True})
            mock_post.assert_called_once_with("http://example.com", headers={}, data={"a": 1}, files={"f": b"x"})

    def test_request_url_unknown_method(self):
        with mock.patch('athina.url.time.sleep'):
            result = request_url("http://example.com", method="delete")
            self.assertIsNone(result)

    def test_request_url_connection_error(self):
        with mock.patch('athina.url.time.sleep'), \
                mock.patch('athina.url.requests.get', side_effect=requests.exceptions.ConnectionError):
            result = request_url("http://example.com", method="get")
            self.assertEqual(result, {})

    def test_request_url_missing_schema(self):
        with mock.patch('athina.url.time.sleep'), \
                mock.patch('athina.url.requests.get', side_effect=requests.exceptions.MissingSchema):
            result = request_url("example.com", method="get")
            self.assertEqual(result, {})

    def test_return_requested_type_json(self):
        class FakeResp:
            def json(self):
                return {"a": 1}
        self.assertEqual(_return_requested_type(FakeResp(), "json"), {"a": 1})

    def test_return_requested_type_json_error(self):
        class FakeResp:
            def json(self):
                raise json.decoder.JSONDecodeError("x", "y", 1)
        self.assertEqual(_return_requested_type(FakeResp(), "json"), {})

    def test_return_requested_type_json_attribute_error(self):
        self.assertEqual(_return_requested_type("", "json"), {})

    def test_return_requested_type_text(self):
        class FakeResp:
            text = "hello"
        self.assertEqual(_return_requested_type(FakeResp(), "text"), "hello")

    def test_return_requested_type_text_error(self):
        class FakeResp:
            @property
            def text(self):
                raise Exception("boom")
        self.assertEqual(_return_requested_type(FakeResp(), "text"), "")

    def test_return_requested_type_other(self):
        self.assertIsNone(_return_requested_type(None, "xml"))
