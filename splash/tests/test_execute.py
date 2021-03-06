# -*- coding: utf-8 -*-
from __future__ import absolute_import
import json
import unittest
from cStringIO import StringIO

from PIL import Image
import requests
import pytest

from . import test_render
from .test_jsonpost import JsonPostRequestHandler
from .utils import NON_EXISTING_RESOLVABLE, SplashServer
from .mockserver import JsRender


class BaseLuaRenderTest(test_render.BaseRenderTest):
    endpoint = 'execute'

    def request_lua(self, code, query=None):
        q = {"lua_source": code}
        q.update(query or {})
        return self.request(q)

    def assertErrorLineNumber(self, resp, linenum):
        self.assertStatusCode(resp, 400)
        self.assertIn(":%d:" % linenum, resp.text)


class MainFunctionTest(BaseLuaRenderTest):

    def test_return_json(self):
        resp = self.request_lua("""
        function main(splash)
          local obj = {key="value"}
          return {
            mystatus="ok",
            number=5,
            float=-0.5,
            obj=obj,
            bool=true,
            bool2=false,
            missing=nil
          }
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.headers['content-type'], 'application/json')
        self.assertEqual(resp.json(), {
            "mystatus": "ok",
            "number": 5,
            "float": -0.5,
            "obj": {"key": "value"},
            "bool": True,
            "bool2": False,
        })

    def test_unicode(self):
        resp = self.request_lua(u"""
        function main(splash) return {key="значение"} end
        """.encode('utf8'))

        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.headers['content-type'], 'application/json')
        self.assertEqual(resp.json(), {"key": u"значение"})

    def test_unicode_direct(self):
        resp = self.request_lua(u"""
        function main(splash)
          return 'привет'
        end
        """.encode('utf8'))
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, u"привет")
        self.assertEqual(resp.headers['content-type'], 'text/plain; charset=utf-8')

    def test_number(self):
        resp = self.request_lua("function main(splash) return 1 end")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "1")
        self.assertEqual(resp.headers['content-type'], 'text/plain; charset=utf-8')

    def test_number_float(self):
        resp = self.request_lua("function main(splash) return 1.5 end")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "1.5")
        self.assertEqual(resp.headers['content-type'], 'text/plain; charset=utf-8')

    def test_bool(self):
        resp = self.request_lua("function main(splash) return true end")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "True")
        self.assertEqual(resp.headers['content-type'], 'text/plain; charset=utf-8')

    def test_empty(self):
        resp = self.request_lua("function main(splash) end")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "")

        resp = self.request_lua("function main() end")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "")

    def test_no_main(self):
        resp = self.request_lua("x=1")
        self.assertStatusCode(resp, 400)
        self.assertIn("function is not found", resp.text)

    def test_bad_main(self):
        resp = self.request_lua("main=1")
        self.assertStatusCode(resp, 400)
        self.assertIn("is not a function", resp.text)

    def test_ugly_main(self):
        resp = self.request_lua("main={coroutine=123}")
        self.assertStatusCode(resp, 400)
        self.assertIn("is not a function", resp.text)

    def test_nasty_main(self):
        resp = self.request_lua("""
        main = {coroutine=function()
          return {
            send=function() end,
            next=function() end
          }
        end}
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("is not a function", resp.text)


class ResultContentTypeTest(BaseLuaRenderTest):
    def test_content_type(self):
        resp = self.request_lua("""
        function main(splash)
          splash:set_result_content_type('text/plain')
          return "hi!"
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.headers['content-type'], 'text/plain')
        self.assertEqual(resp.text, 'hi!')

    def test_content_type_ignored_for_tables(self):
        resp = self.request_lua("""
        function main(splash)
          splash:set_result_content_type('text/plain')
          return {hi="hi!"}
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.headers['content-type'], 'application/json')
        self.assertEqual(resp.text, '{"hi": "hi!"}')

    def test_bad_content_type(self):
        resp = self.request_lua("""
        function main(splash)
          splash:set_result_content_type(55)
          return "hi!"
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("argument must be a string", resp.text)

        resp = self.request_lua("""
        function main(splash)
          splash:set_result_content_type()
          return "hi!"
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("set_result_content_type", resp.text)

    def test_bad_content_type_func(self):
        resp = self.request_lua("""
        function main(splash)
          splash:set_result_content_type(function () end)
          return "hi!"
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("argument must be a string", resp.text)


class ErrorsTest(BaseLuaRenderTest):

    def test_syntax_error(self):
        resp = self.request_lua("function main(splash) sdhgfsajhdgfjsahgd end")
        self.assertStatusCode(resp, 400)

    def test_syntax_error_toplevel(self):
        resp = self.request_lua("sdg; function main(splash) sdhgfsajhdgfjsahgd end")
        self.assertStatusCode(resp, 400)

    def test_unicode_error(self):
        resp = self.request_lua(u"function main(splash) 'привет' end".encode('utf8'))
        self.assertStatusCode(resp, 400)
        self.assertIn("unexpected symbol", resp.text)

    def test_user_error(self):
        resp = self.request_lua("""
        function main(splash)
          error("User Error Happened")
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("User Error Happened", resp.text)

    def test_bad_splash_attribute(self):
        resp = self.request_lua("""
        function main(splash)
          local x = splash.foo
          return x == nil
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "True")

    def test_return_multiple(self):
        resp = self.request_lua("function main(splash) return 'foo', 'bar' end")
        self.assertStatusCode(resp, 400)
        self.assertIn("must return a single result", resp.text)

    def test_return_splash(self):
        resp = self.request_lua("function main(splash) return splash end")
        self.assertStatusCode(resp, 400)

    def test_return_function(self):
        resp = self.request_lua("function main(splash) return function() end end")
        self.assertStatusCode(resp, 400)
        self.assertIn("function objects are not allowed", resp.text)

    def test_return_coroutine(self):
        resp = self.request_lua("""
        function main(splash)
          return coroutine.create(function() end)
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("(a nil value)", resp.text)

    def test_return_coroutine_nosandbox(self):
        with SplashServer(extra_args=['--disable-lua-sandbox']) as splash:
            resp = requests.get(
                url=splash.url("execute"),
                params={
                    'lua_source': """
                        function main(splash)
                            return coroutine.create(function() end)
                        end
                    """
                },
            )
            self.assertStatusCode(resp, 400)
            self.assertIn("function objects are not allowed", resp.text)

    def test_return_started_coroutine(self):
        resp = self.request_lua("""
        function main(splash)
          local co = coroutine.create(function()
            coroutine.yield()
          end)
          coroutine.resume(co)
          return co
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("(a nil value)", resp.text)

    def test_return_started_coroutine_nosandbox(self):
        with SplashServer(extra_args=['--disable-lua-sandbox']) as splash:
            resp = requests.get(
                url=splash.url("execute"),
                params={
                    'lua_source': """
                        function main(splash)
                          local co = coroutine.create(function()
                            coroutine.yield()
                          end)
                          coroutine.resume(co)
                          return co
                        end
                    """
                },
            )
            self.assertStatusCode(resp, 400)
            self.assertIn("thread objects are not allowed", resp.text)

    def test_error_line_number_attribute_access(self):
        resp = self.request_lua("""
        function main(splash)
           local x = 5
           splash.set_result_content_type("hello")
        end
        """)
        self.assertErrorLineNumber(resp, 4)

    def test_error_line_number_bad_argument(self):
        resp = self.request_lua("""
        function main(splash)
           local x = 5
           splash:set_result_content_type(48)
        end
        """)
        self.assertErrorLineNumber(resp, 4)

    def test_error_line_number_wrong_keyword_argument(self):
        resp = self.request_lua("""  -- 1
        function main(splash)        -- 2
           splash:wait{timeout=0.7}  -- 3 <--
        end                          -- 4
        """)                       # -- 5
        self.assertErrorLineNumber(resp, 3)

    def test_pcall_wrong_keyword_arguments(self):
        resp = self.request_lua("""
        function main(splash)
           local x = function()
               return splash:wait{timeout=0.7}
           end
           local ok, res = pcall(x)
           return {ok=ok, res=res}
        end
        """)
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertEqual(data["ok"], False)



class RunjsTest(BaseLuaRenderTest):

    def assertRunjsResult(self, js, result, type):
        resp = self.request_lua("""
        function main(splash)
            local res = splash:runjs([[%s]])
            return {res=res, tp=type(res)}
        end
        """ % js)
        self.assertStatusCode(resp, 200)
        if result is None:
            self.assertEqual(resp.json(), {'tp': type})
        else:
            self.assertEqual(resp.json(), {'res': result, 'tp': type})

    def test_numbers(self):
        self.assertRunjsResult("1.0", 1.0, "number")
        self.assertRunjsResult("1", 1, "number")
        self.assertRunjsResult("1+2", 3, "number")

    def test_inf(self):
        self.assertRunjsResult("1/0", float('inf'), "number")
        self.assertRunjsResult("-1/0", float('-inf'), "number")

    def test_string(self):
        self.assertRunjsResult("'foo'", u'foo', 'string')

    def test_bool(self):
        self.assertRunjsResult("true", True, 'boolean')

    def test_undefined(self):
        self.assertRunjsResult("undefined", None, 'nil')

    def test_null(self):
        # XXX: null is converted to an empty string by QT,
        # we can't distinguish it from a "real" empty string.
        self.assertRunjsResult("null", "", 'string')

    def test_unicode_string(self):
        self.assertRunjsResult("'привет'", u'привет', 'string')

    def test_unicode_string_in_object(self):
        self.assertRunjsResult(
            'var o={}; o["ключ"] = "значение"; o',
            {u'ключ': u'значение'},
            'table'
        )

    def test_nested_object(self):
        self.assertRunjsResult(
            'var o={}; o["x"] = {}; o["x"]["y"] = 5; o["z"] = "foo"; o',
            {"x": {"y": 5}, "z": "foo"},
            'table'
        )

    def test_array(self):
        self.assertRunjsResult(
            'x = [3, 2, 1, "foo", ["foo", [], "bar"], {}]; x',
            [3, 2, 1, "foo", ["foo", [], "bar"], {}],
            'table',
        )

    def test_self_referencing(self):
        self.assertRunjsResult(
            'var o={}; o["x"] = "5"; o["y"] = o; o',
            {"x": "5"},  # self reference is discarded
            'table'
        )

    def test_function(self):
        # XXX: functions are not returned by QT
        self.assertRunjsResult(
            "x = function(){return 5}; x",
            {},
            "table"
        )

        self.assertRunjsResult(
            "function(){return 5}",
            None,
            "nil"
        )

    def test_object_with_function(self):
        # XXX: complex objects are unsupported
        self.assertRunjsResult(
            '{"x":2, "y": function(){}}',
            None,
            "nil",
        )

    def test_function_call(self):
        self.assertRunjsResult(
            "function x(){return 5}; x();",
            5,
            "number"
        )

    def test_dateobj(self):
        # XXX: Date objects are converted to ISO8061 strings.
        # Does it make sense to do anything else with them?
        # E.g. make them available to Lua as tables?
        self.assertRunjsResult(
            'x = new Date("21 May 1958 10:12 UTC"); x',
            "1958-05-21T10:12:00Z",
            "string"
        )

    def test_regexp(self):
        self.assertRunjsResult(
            '/my-regexp/i',
            {
                u'_jstype': u'RegExp',
                'caseSensitive': False,
                'pattern': u'my-regexp'
            },
            'table'
        )

        self.assertRunjsResult(
            '/my-regexp/',
            {
                u'_jstype': u'RegExp',
                'caseSensitive': True,
                'pattern': u'my-regexp'
            },
            'table'
        )


class JsfuncTest(BaseLuaRenderTest):
    def assertJsfuncResult(self, source, arguments, result):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc([[%s]])
            return func(%s)
        end
        """ % (source, arguments))
        self.assertStatusCode(resp, 200)
        if isinstance(result, (dict, list)):
            self.assertEqual(resp.json(), result)
        else:
            self.assertEqual(resp.text, result)

    def test_Math(self):
        self.assertJsfuncResult("Math.pow", "5, 2", "25")

    def test_helloworld(self):
        self.assertJsfuncResult(
            "function(s) {return 'Hello, ' + s;}",
            "'world!'",
            "Hello, world!"
        )

    def test_object_argument(self):
        self.assertJsfuncResult(
            "function(obj) {return obj.foo;}",
            "{foo='bar'}",
            "bar",
        )

    def test_object_result(self):
        self.assertJsfuncResult(
            "function(obj) {return obj.foo;}",
            "{foo={x=5, y=10}}",
            {"x": 5, "y": 10},
        )

    def test_object_result_pass(self):
        resp = self.request_lua("""
        function main(splash)
            local func1 = splash:jsfunc("function(){return {foo:{x:5}}}")
            local func2 = splash:jsfunc("function(obj){return obj.foo}")
            local obj = func1()
            return func2(obj)
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"x": 5})

    def test_bool(self):
        is5 = "function(num){return num==5}"
        self.assertJsfuncResult(is5, "5", "True")
        self.assertJsfuncResult(is5, "6", "False")

    def test_undefined_result(self):
        self.assertJsfuncResult("function(){}", "", "None")

    def test_undefined_argument(self):
        self.assertJsfuncResult("function(foo){return foo}", "", "None")

    def test_throw_string(self):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc("function(){throw 'ABC'}")
            return func()
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("error during JS function call: u'ABC'", resp.text)

    def test_throw_pcall(self):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc("function(){throw 'ABC'}")
            local ok, res = pcall(func)
            return {ok=ok, res=res}
        end
        """)
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertEqual(data["ok"], False)
        self.assertIn("error during JS function call: u'ABC'", data["res"])

    def test_throw_error(self):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc("function(){throw new Error('ABC')}")
            return func()
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("error during JS function call: u'Error: ABC'", resp.text)

    def test_throw_error_pcall(self):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc("function(){throw new Error('ABC')}")
            local ok, res = pcall(func)
            return {ok=ok, res=res}
        end
        """)
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertEqual(data["ok"], False)
        self.assertIn("error during JS function call: u'Error: ABC'", data["res"])

    def test_js_syntax_error(self):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc("function(){")
            return func()
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("error during JS function call", resp.text)
        self.assertIn("SyntaxError", resp.text)

    def test_array_result(self):
        self.assertJsfuncResult(
            "function(){return [1, 2, 'foo']}",
            "",
            [1, 2, "foo"]
        )

    def test_array_result_processed(self):
        # XXX: note that index is started from 1
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc("function(){return [1, 2, 'foo']}")
            local arr = func()
            local first = arr[1]
            return {arr=arr, first=1, tp=type(arr)}
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"arr": [1, 2, "foo"], "first": 1, "tp": "table"})

    def test_array_argument(self):
        # XXX: note that index is started from 1
        self.assertJsfuncResult(
            "function(arr){return arr[1]}",
            "{5, 6, 'foo'}",
            "5",
        )

    # this doesn't work because table is passed as an object
    @pytest.mark.xfail
    def test_array_length(self):
        self.assertJsfuncResult(
            "function(arr){return arr.length}",
            "{5, 6, 'foo'}",
            "3",
        )

    def test_jsfunc_attributes(self):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc("function(){return 123}")
            return func.source
        end
        """)
        self.assertStatusCode(resp, 400)

    def test_jsfunc_private_attributes(self):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc_private("function(){return 123}")
            return func.source
        end
        """)
        self.assertStatusCode(resp, 400)

    # see https://github.com/scoder/lupa/pull/46
    @pytest.mark.xfail
    def test_jsfunc_private_attributes_error_message(self):
        resp = self.request_lua("""
        function main(splash)
            local func = splash:jsfunc_private("function(){return 123}")
            return func.source
        end
        """)
        self.assertNotIn("str()", resp.text)
        self.assertIn("AttributeError", resp.text)


class WaitTest(BaseLuaRenderTest):

    def wait(self, wait_args, request_args=None):
        code = """
        function main(splash)
          local ok, reason = splash:wait%s
          return {ok=ok, reason=reason}
        end
        """ % wait_args
        return self.request_lua(code, request_args)

    def go_and_wait(self, wait_args, request_args):
        code = """
        function main(splash)
          assert(splash:go(splash.args.url))
          local ok, reason = splash:wait%s
          return {ok=ok, reason=reason}
        end
        """ % wait_args
        return self.request_lua(code, request_args)

    def test_timeout(self):
        resp = self.wait("(0.01)", {"timeout": 0.1})
        self.assertStatusCode(resp, 200)

        resp = self.wait("(1)", {"timeout": 0.1})
        self.assertStatusCode(resp, 504)

    def test_wait_success(self):
        resp = self.wait("(0.01)")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"ok": True})

    def test_wait_noredirect(self):
        resp = self.wait("{time=0.01, cancel_on_redirect=true}")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"ok": True})

    def test_wait_redirect_nocancel(self):
        # jsredirect-timer redirects after 0.1ms
        resp = self.go_and_wait(
            "{time=0.2, cancel_on_redirect=false}",
            {'url': self.mockurl("jsredirect-timer")}
        )
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"ok": True})

    def test_wait_redirect_cancel(self):
        # jsredirect-timer redirects after 0.1ms
        resp = self.go_and_wait(
            "{time=0.2, cancel_on_redirect=true}",
            {'url': self.mockurl("jsredirect-timer")}
        )
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"reason": "redirect"})  # ok is nil

    @unittest.skipIf(NON_EXISTING_RESOLVABLE, "non existing hosts are resolvable")
    def test_wait_onerror(self):
        resp = self.go_and_wait(
            "{time=0.2, cancel_on_redirect=false, cancel_on_error=true}",
            {'url': self.mockurl("jsredirect-non-existing")}
        )
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"reason": "error"})  # ok is nil

    @unittest.skipIf(NON_EXISTING_RESOLVABLE, "non existing hosts are resolvable")
    def test_wait_onerror_nocancel(self):
        resp = self.go_and_wait(
            "{time=0.2, cancel_on_redirect=false, cancel_on_error=false}",
            {'url': self.mockurl("jsredirect-non-existing")}
        )
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"ok": True})

    @unittest.skipIf(NON_EXISTING_RESOLVABLE, "non existing hosts are resolvable")
    def test_wait_onerror_nocancel_redirect(self):
        resp = self.go_and_wait(
            "{time=0.2, cancel_on_redirect=true, cancel_on_error=false}",
            {'url': self.mockurl("jsredirect-non-existing")}
        )
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"reason": "redirect"})

    def test_wait_badarg(self):
        resp = self.wait('{time="sdf"}')
        self.assertStatusCode(resp, 400)

    def test_wait_badarg2(self):
        resp = self.wait('{time="sdf"}')
        self.assertStatusCode(resp, 400)

    def test_wait_good_string(self):
        resp = self.wait('{time="0.01"}')
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"ok": True})

    def test_wait_noargs(self):
        resp = self.wait('()')
        self.assertStatusCode(resp, 400)

    def test_wait_time_missing(self):
        resp = self.wait('{cancel_on_redirect=false}')
        self.assertStatusCode(resp, 400)

    def test_wait_unknown_args(self):
        resp = self.wait('{ttime=0.5}')
        self.assertStatusCode(resp, 400)

    def test_wait_negative(self):
        resp = self.wait('(-0.2)')
        self.assertStatusCode(resp, 400)


class ArgsTest(BaseLuaRenderTest):
    def args_request(self, query):
        func = """
        function main(splash)
          return {args=splash.args}
        end
        """
        return self.request_lua(func, query)

    def assertArgs(self, query):
        resp = self.args_request(query)
        self.assertStatusCode(resp, 200)
        data = resp.json()["args"]
        data.pop('lua_source')
        data.pop('uid')
        return data

    def assertArgsPassed(self, query):
        args = self.assertArgs(query)
        self.assertEqual(args, query)
        return args

    def test_known_args(self):
        self.assertArgsPassed({"wait": "1.0"})
        self.assertArgsPassed({"timeout": "2.0"})
        self.assertArgsPassed({"url": "foo"})

    def test_unknown_args(self):
        self.assertArgsPassed({"foo": "bar"})

    def test_filters_validation(self):
        # 'global' known arguments are still validated
        resp = self.args_request({"filters": 'foo,bar'})
        self.assertStatusCode(resp, 400)
        self.assertIn("Invalid filter names", resp.text)


class JsonPostArgsTest(ArgsTest):
    request_handler = JsonPostRequestHandler

    def test_headers(self):
        headers = {"user-agent": "Firefox", "content-type": "text/plain"}
        self.assertArgsPassed({"headers": headers})

    def test_headers_items(self):
        headers = [["user-agent", "Firefox"], ["content-type", "text/plain"]]
        self.assertArgsPassed({"headers": headers})

    def test_access_headers(self):
        func = """
        function main(splash)
          local ua = "Unknown"
          if splash.args.headers then
            ua = splash.args.headers['user-agent']
          end
          return {ua=ua, firefox=(ua=="Firefox")}
        end
        """
        resp = self.request_lua(func, {'headers': {"user-agent": "Firefox"}})
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"ua": "Firefox", "firefox": True})

        resp = self.request_lua(func)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"ua": "Unknown", "firefox": False})

    def test_custom_object(self):
        self.assertArgsPassed({"myobj": {"foo": "bar", "bar": ["egg", "spam", 1]}})

    def test_post_numbers(self):
        self.assertArgsPassed({"x": 5})


class GoTest(BaseLuaRenderTest):

    def go_status(self, url):
        resp = self.request_lua("""
        function main(splash)
            local ok, reason = splash:go(splash.args.url)
            return {ok=ok, reason=reason}
        end
        """, {"url": url})
        self.assertStatusCode(resp, 200)
        return resp.json()

    def assertGoStatusCodeError(self, code):
        data = self.go_status(self.mockurl("getrequest?code=%s" % code))
        self.assertNotIn("ok", data)
        self.assertEqual(data["reason"], "http%s" % code)

    def assertGoNoError(self, code):
        data = self.go_status(self.mockurl("getrequest?code=%s" % code))
        self.assertTrue(data["ok"])
        self.assertNotIn("reason", data)

    def test_go_200(self):
        self.assertGoNoError(200)

    def test_go_400(self):
        self.assertGoStatusCodeError(400)

    def test_go_401(self):
        self.assertGoStatusCodeError(401)

    def test_go_403(self):
        self.assertGoStatusCodeError(403)

    def test_go_404(self):
        self.assertGoStatusCodeError(404)

    def test_go_500(self):
        self.assertGoStatusCodeError(500)

    def test_go_503(self):
        self.assertGoStatusCodeError(503)

    def test_nourl(self):
        resp = self.request_lua("function main(splash) splash:go() end")
        self.assertStatusCode(resp, 400)

    def test_nourl_args(self):
        resp = self.request_lua("function main(splash) splash:go(splash.args.url) end")
        self.assertStatusCode(resp, 400)

    @unittest.skipIf(NON_EXISTING_RESOLVABLE, "non existing hosts are resolvable")
    def test_go_error(self):
        data = self.go_status("non-existing")
        self.assertEqual(data.get('ok', False), False)
        self.assertEqual(data["reason"], "error")

    def test_go_multiple(self):
        resp = self.request_lua("""
        function main(splash)
            splash:go(splash.args.url_1)
            local html_1 = splash:html()
            splash:go(splash.args.url_2)
            return {html_1=html_1, html_2=splash:html()}
        end
        """, {
            'url_1': self.mockurl('getrequest?foo=1'),
            'url_2': self.mockurl('getrequest?bar=2')
        })
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertIn("{'foo': ['1']}", data['html_1'])
        self.assertIn("{'bar': ['2']}", data['html_2'])

    def test_go_bad_then_good(self):
        resp = self.request_lua("""
        function main(splash)
            local ok1, err1 = splash:go(splash.args.url_1)
            local html_1 = splash:html()
            local ok2, err2 = splash:go(splash.args.url_2)
            local html_2 = splash:html()
            return {html_1=html_1, html_2=html_2, err1=err1, err2=err2, ok1=ok1, ok2=ok2}
        end
        """, {
            'url_1': self.mockurl('--some-non-existing-resource--'),
            'url_2': self.mockurl('bad-related'),
        })
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertEqual(data["err1"], "http404")
        self.assertNotIn("err2", data)

        self.assertNotIn("ok1", data)
        self.assertEqual(data["ok2"], True)

        self.assertIn("No Such Resource", data["html_1"])
        self.assertIn("http://non-existing", data["html_2"])

    def test_go_headers_cookie(self):
        resp = self.request_lua("""
        function main(splash)
            assert(splash:go{splash.args.url, headers={
                ["Cookie"] = "foo=bar; egg=spam"
            }})
            return splash:html()
        end
        """, {"url": self.mockurl("get-cookie?key=egg")})
        self.assertStatusCode(resp, 200)
        self.assertIn("spam", resp.text)

    def test_go_headers(self):
        resp = self.request_lua("""
        function main(splash)
            assert(splash:go{splash.args.url, headers={
                ["Custom-Header"] = "Header Value",
            }})
            local res1 = splash:html()

            -- second request is without any custom headers
            assert(splash:go(splash.args.url))
            local res2 = splash:html()

            return {res1=res1, res2=res2}
        end
        """, {"url": self.mockurl("getrequest")})
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertIn("'Header Value'", data["res1"])
        self.assertNotIn("'Header Value'", data["res2"])

    def test_set_custom_headers(self):
        resp = self.request_lua("""
        function main(splash)
            splash:set_custom_headers({
                ["Header-1"] = "Value 1",
                ["Header-2"] = "Value 2",
            })

            assert(splash:go(splash.args.url))
            local res1 = splash:html()

            assert(splash:go{splash.args.url, headers={
                ["Header-3"] = "Value 3",
            }})
            local res2 = splash:html()

            assert(splash:go(splash.args.url))
            local res3 = splash:html()

            return {res1=res1, res2=res2, res3=res3}
        end
        """, {"url": self.mockurl("getrequest")})
        self.assertStatusCode(resp, 200)
        data = resp.json()

        self.assertIn("'Value 1'", data["res1"])
        self.assertIn("'Value 2'", data["res1"])
        self.assertNotIn("'Value 3'", data["res1"])

        self.assertNotIn("'Value 1'", data["res2"])
        self.assertNotIn("'Value 2'", data["res2"])
        self.assertIn("'Value 3'", data["res2"])

        self.assertIn("'Value 1'", data["res3"])
        self.assertIn("'Value 2'", data["res3"])
        self.assertNotIn("'Value 3'", data["res3"])


class SetUserAgentTest(BaseLuaRenderTest):
    def test_set_user_agent(self):
        resp = self.request_lua("""
        function main(splash)
            splash:go(splash.args.url)
            local res1 = splash:html()

            splash:set_user_agent("Foozilla")
            splash:go(splash.args.url)
            local res2 = splash:html()

            splash:go(splash.args.url)
            local res3 = splash:html()

            return {res1=res1, res2=res2, res3=res3}
        end
        """, {"url": self.mockurl("getrequest")})

        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertIn("Mozilla", data["res1"])
        self.assertNotIn("Mozilla", data["res2"])
        self.assertNotIn("Mozilla", data["res3"])

        self.assertNotIn("'user-agent': 'Foozilla'", data["res1"])
        self.assertIn("'user-agent': 'Foozilla'", data["res2"])
        self.assertIn("'user-agent': 'Foozilla'", data["res3"])


class CookiesTest(BaseLuaRenderTest):
    def test_cookies(self):
        resp = self.request_lua("""
        function main(splash)
            local function cookies_after(url)
                splash:go(url)
                return splash:get_cookies()
            end

            local c0 = splash:get_cookies()
            local c1 = cookies_after(splash.args.url_1)
            local c2 = cookies_after(splash.args.url_2)

            splash:clear_cookies()
            local c3 = splash:get_cookies()

            local c4 = cookies_after(splash.args.url_2)
            local c5 = cookies_after(splash.args.url_1)

            splash:delete_cookies("foo")
            local c6 = splash:get_cookies()

            splash:delete_cookies{url="http://example.com"}
            local c7 = splash:get_cookies()

            splash:delete_cookies{url="http://localhost"}
            local c8 = splash:get_cookies()

            splash:init_cookies(c2)
            local c9 = splash:get_cookies()

            return {c0=c0, c1=c1, c2=c2, c3=c3, c4=c4, c5=c5, c6=c6, c7=c7, c8=c8, c9=c9}
        end
        """, {
            "url_1": self.mockurl("set-cookie?key=foo&value=bar"),
            "url_2": self.mockurl("set-cookie?key=egg&value=spam"),
        })

        self.assertStatusCode(resp, 200)
        data = resp.json()

        cookie1 = {
            'name': 'foo',
            'value': 'bar',
            'domain': 'localhost',
            'path': '/',
            'httpOnly': False,
            'secure': False
        }
        cookie2 = {
            'name': 'egg',
            'value': 'spam',
            'domain': 'localhost',
            'path': '/',
            'httpOnly': False,
            'secure': False
        }

        self.assertEqual(data["c0"], [])
        self.assertEqual(data["c1"], [cookie1])
        self.assertEqual(data["c2"], [cookie1, cookie2])
        self.assertEqual(data["c3"], [])
        self.assertEqual(data["c4"], [cookie2])
        self.assertEqual(data["c5"], [cookie2, cookie1])
        self.assertEqual(data["c6"], [cookie2])
        self.assertEqual(data["c7"], [cookie2])
        self.assertEqual(data["c8"], [])
        self.assertEqual(data["c9"], data["c2"])

    def test_add_cookie(self):
        resp = self.request_lua("""
        function main(splash)
            splash:add_cookie("baz", "egg")
            splash:add_cookie{"spam", "egg", domain="example.com"}
            splash:add_cookie{
                name="foo",
                value="bar",
                path="/",
                domain="localhost",
                expires="2016-07-24T19:20:30+02:00",
                secure=true,
                httpOnly=true,
            }
            return splash:get_cookies()
        end""")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), [
            {"name": "baz", "value": "egg", "path": "",
             "domain": "", "httpOnly": False, "secure": False},
            {"name": "spam", "value": "egg", "path": "",
             "domain": "example.com", "httpOnly": False, "secure": False},
            {"name": "foo", "value": "bar", "path": "/",
             "domain": "localhost", "httpOnly": True, "secure": True,
             "expires": "2016-07-24T19:20:30+02:00"},
        ])

    def test_init_cookies(self):
        resp = self.request_lua("""
        function main(splash)
            splash:init_cookies({
                {name="baz", value="egg"},
                {name="spam", value="egg", domain="example.com"},
                {
                    name="foo",
                    value="bar",
                    path="/",
                    domain="localhost",
                    expires="2016-07-24T19:20:30+02:00",
                    secure=true,
                    httpOnly=true,
                }
            })
            return splash:get_cookies()
        end""")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), [
            {"name": "baz", "value": "egg", "path": "",
             "domain": "", "httpOnly": False, "secure": False},
            {"name": "spam", "value": "egg", "path": "",
             "domain": "example.com", "httpOnly": False, "secure": False},
            {"name": "foo", "value": "bar", "path": "/",
             "domain": "localhost", "httpOnly": True, "secure": True,
             "expires": "2016-07-24T19:20:30+02:00"},
        ])


class CurrentUrlTest(BaseLuaRenderTest):

    def request_url(self, url, wait=0.0):
        return self.request_lua("""
        function main(splash)
            local ok, res = splash:go(splash.args.url)
            splash:wait(splash.args.wait)
            return {ok=ok, res=res, url=splash:url()}
        end
        """, {"url": url, "wait": wait})

    def assertCurrentUrl(self, go_url, url=None, wait=0.0):
        if url is None:
            url = go_url
        resp = self.request_url(go_url, wait)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json()["url"], url)

    def test_start(self):
        resp = self.request_lua("function main(splash) return splash:url() end")
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "")

    def test_blank(self):
        self.assertCurrentUrl("about:blank")

    def test_not_redirect(self):
        self.assertCurrentUrl(self.mockurl("getrequest"))

    def test_jsredirect(self):
        self.assertCurrentUrl(self.mockurl("jsredirect"))
        self.assertCurrentUrl(
            self.mockurl("jsredirect"),
            self.mockurl("jsredirect-target"),
            wait=0.5,
        )


class DisableScriptsTest(BaseLuaRenderTest):

    def test_nolua(self):
        with SplashServer(extra_args=['--disable-lua']) as splash:

            # Check that Lua is disabled in UI
            resp = requests.get(splash.url("/"))
            self.assertStatusCode(resp, 200)
            self.assertNotIn("<textarea", resp.text)  # no code editor

            script = "function main(splash) return 'foo' end"

            # Check that /execute doesn't work
            resp = requests.get(
                url=splash.url("execute"),
                params={'lua_source': script},
            )
            self.assertStatusCode(resp, 404)


class SandboxTest(BaseLuaRenderTest):

    def assertTooMuchCPU(self, resp):
        self.assertStatusCode(resp, 400)
        self.assertIn("script uses too much CPU", resp.text)

    def assertTooMuchMemory(self, resp):
        self.assertStatusCode(resp, 400)
        self.assertIn("script uses too much memory", resp.text)

    def test_sandbox_string_function(self):
        resp = self.request_lua("""
        function main(self)
            return string.rep("x", 10000)
        end
        """)
        self.assertErrorLineNumber(resp, 3)
        self.assertIn("rep", resp.text)
        self.assertIn("(a nil value)", resp.text)

    def test_sandbox_string_method(self):
        resp = self.request_lua("""
        function main(self)
            return ("x"):rep(10000)
        end
        """)
        self.assertErrorLineNumber(resp, 3)
        self.assertIn("attempt to index constant", resp.text)

    # TODO: strings should use a sandboxed string module as a metatable
    @pytest.mark.xfail
    def test_non_sandboxed_string_method(self):
        resp = self.request_lua("""
        function main(self)
            return ("X"):lower()
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "x")

    def test_infinite_loop(self):
        resp = self.request_lua("""
        function main(self)
            local x = 0
            while true do
                x = x + 1
            end
            return x
        end
        """)
        self.assertTooMuchCPU(resp)

    def test_infinite_loop_toplevel(self):
        resp = self.request_lua("""
        x = 0
        while true do
            x = x + 1
        end
        function main(self)
            return 5
        end
        """)
        self.assertTooMuchCPU(resp)

    def test_infinite_loop_memory(self):
        resp = self.request_lua("""
        function main(self)
            t = {}
            while true do
                t = { t }
            end
            return t
        end
        """)
        self.assertStatusCode(resp, 400)
        self.assertIn("too much", resp.text)  # it can be either memory or CPU

    def test_memory_attack(self):
        resp = self.request_lua("""
        function main(self)
            local s = "aaaaaaaaaaaaaaaaaaaa"
            while true do
                s = s..s
            end
            return s
        end
        """)
        self.assertTooMuchMemory(resp)

    def test_memory_attack_toplevel(self):
        resp = self.request_lua("""
        s = "aaaaaaaaaaaaaaaaaaaa"
        while true do
            s = s..s
        end
        function main(self)
            return s
        end
        """)
        self.assertTooMuchMemory(resp)

    def test_billion_laughs(self):
        resp = self.request_lua("""
        s = "s"
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s s = s .. s
        function main() end
        """)
        self.assertTooMuchMemory(resp)

    def test_disable_sandbox(self):
        # dofile function should be always sandboxed
        is_sandbox = "function main(splash) return {s=(dofile==nil)} end"

        resp = self.request_lua(is_sandbox)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"s": True})

        with SplashServer(extra_args=['--disable-lua-sandbox']) as splash:
            resp = requests.get(
                url=splash.url("execute"),
                params={'lua_source': is_sandbox},
            )
            self.assertStatusCode(resp, 200)
            self.assertEqual(resp.json(), {"s": False})


class RequireTest(BaseLuaRenderTest):
    def _set_title(self, title):
        return """
        splash:set_content([[
        <html>
          <head>
            <title>%s</title>
          </head>
        </html>
        ]])
        """ % title

    def assertNoRequirePathsLeaked(self, resp):
        self.assertNotIn("/lua", resp.text)
        self.assertNotIn("init.lua", resp.text)

    def test_splash_patching(self):
        title = "TEST"
        resp = self.request_lua("""
        require "utils_patch"
        function main(splash)
            %(set_title)s
            return splash:get_document_title()
        end
        """ % dict(set_title=self._set_title(title)))
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, title)

    def test_splash_patching_no_require(self):
        resp = self.request_lua("""
        function main(splash)
            %(set_title)s
            return splash:get_document_title()
        end
        """ % dict(set_title=self._set_title("TEST")))
        self.assertStatusCode(resp, 400)
        self.assertIn("get_document_title", resp.text)
        self.assertNoRequirePathsLeaked(resp)

    def test_require_unsafe(self):
        resp = self.request_lua("""
        local Splash = require("splash")
        function main(splash) return "hello" end
        """)
        self.assertErrorLineNumber(resp, 2)
        self.assertNoRequirePathsLeaked(resp)

    def test_require_not_whitelisted(self):
        resp = self.request_lua("""
        local utils = require("utils")
        local secret = require("secret")
        function main(splash) return "hello" end
        """)
        self.assertErrorLineNumber(resp, 3)
        self.assertNoRequirePathsLeaked(resp)

    def test_require_non_existing(self):
        resp = self.request_lua("""
        local foobar = require("foobar")
        function main(splash) return "hello" end
        """)
        self.assertNoRequirePathsLeaked(resp)
        self.assertErrorLineNumber(resp, 2)

    def test_require_non_existing_whitelisted(self):
        resp = self.request_lua("""
        local non_existing = require("non_existing")
        function main(splash) return "hello" end
        """)
        self.assertNoRequirePathsLeaked(resp)
        self.assertErrorLineNumber(resp, 2)

    def test_module(self):
        title = "TEST"
        resp = self.request_lua("""
        local utils = require "utils"
        function main(splash)
            %(set_title)s
            return utils.get_document_title(splash)
        end
        """ % dict(set_title=self._set_title(title)))
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, title)

    def test_module_require_unsafe_from_safe(self):
        resp = self.request_lua("""
        function main(splash)
            return require("utils").hello
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "world")


class HarTest(BaseLuaRenderTest):
    def test_har_empty(self):
        resp = self.request_lua("""
        function main(splash)
            return splash:har()
        end
        """)
        self.assertStatusCode(resp, 200)
        har = resp.json()["log"]
        self.assertEqual(har["entries"], [])

    def test_har_about_blank(self):
        resp = self.request_lua("""
        function main(splash)
            splash:go("about:blank")
            return splash:har()
        end
        """)
        self.assertStatusCode(resp, 200)
        har = resp.json()["log"]
        self.assertEqual(har["entries"], [])


class AutoloadTest(BaseLuaRenderTest):
    def test_autoload(self):
        resp = self.request_lua("""
        function main(splash)
            assert(splash:autoload("window.FOO = 'bar'"))

            splash:go(splash.args.url)
            local foo1 = splash:runjs("FOO")

            splash:runjs("window.FOO = 'spam'")
            local foo2 = splash:runjs("FOO")

            splash:go(splash.args.url)
            local foo3 = splash:runjs("FOO")
            return {foo1=foo1, foo2=foo2, foo3=foo3}
        end
        """, {"url": self.mockurl("getrequest")})
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertEqual(data, {"foo1": "bar", "foo2": "spam", "foo3": "bar"})

    def test_autoload_remote(self):
        resp = self.request_lua("""
        function main(splash)
            assert(splash:autoload(splash.args.eggspam_url))
            assert(splash:go(splash.args.url))
            local egg = splash:jsfunc("egg")
            return egg()
        end
        """, {
            "url": self.mockurl("getrequest"),
            "eggspam_url": self.mockurl("eggspam.js"),
        })
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "spam")

    def test_autoload_bad(self):
        resp = self.request_lua("""
        function main(splash)
            local ok, reason = splash:autoload(splash.args.bad_url)
            return {ok=ok, reason=reason}
        end
        """, {"bad_url": self.mockurl("--non-existing--")})
        self.assertStatusCode(resp, 200)
        self.assertNotIn("ok", resp.json())
        self.assertIn("404", resp.json()["reason"])

    def test_noargs(self):
        resp = self.request_lua("""
        function main(splash)
            splash:autoload()
        end
        """)
        self.assertStatusCode(resp, 400)


class HttpGetTest(BaseLuaRenderTest):
    def test_get(self):
        resp = self.request_lua("""
        function main(splash)
            local reply = splash:http_get(splash.args.url)
            splash:wait(0.1)
            return reply.content.text
        end
        """, {"url": self.mockurl("jsrender")})
        self.assertStatusCode(resp, 200)
        self.assertEqual(JsRender.template, resp.text)

    def test_bad_url(self):
        resp = self.request_lua("""
        function main(splash)
            return splash:http_get(splash.args.url)
        end
        """, {"url": self.mockurl("--bad-url--")})
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json()["status"], 404)

    def test_headers(self):
        resp = self.request_lua("""
        function main(splash)
            return splash:http_get{
                splash.args.url,
                headers={
                    ["Custom-Header"] = "Header Value",
                }
            }
        end
        """, {"url": self.mockurl("getrequest")})
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertEqual(data["status"], 200)
        self.assertIn("Header Value", data["content"]["text"])

    def test_redirects_follow(self):
        resp = self.request_lua("""
        function main(splash)
            return splash:http_get(splash.args.url)
        end
        """, {"url": self.mockurl("http-redirect?code=302")})
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertEqual(data["status"], 200)
        self.assertNotIn("redirect to", data["content"]["text"])
        self.assertIn("GET request", data["content"]["text"])

    def test_redirects_nofollow(self):
        resp = self.request_lua("""
        function main(splash)
            return splash:http_get{url=splash.args.url, follow_redirects=false}
        end
        """, {"url": self.mockurl("http-redirect?code=302")})
        self.assertStatusCode(resp, 200)
        data = resp.json()
        self.assertEqual(data["status"], 302)
        self.assertEqual(data["redirectURL"], "/getrequest?http_code=302")
        self.assertIn("302 redirect to", data["content"]["text"])

    def test_noargs(self):
        resp = self.request_lua("""
        function main(splash)
            splash:http_get()
        end
        """)
        self.assertStatusCode(resp, 400)


class NavigationLockingTest(BaseLuaRenderTest):
    def test_lock_navigation(self):
        url = self.mockurl("jsredirect")
        resp = self.request_lua("""
        function main(splash)
            splash:go(splash.args.url)
            splash:lock_navigation()
            splash:wait(0.3)
            return splash:url()
        end
        """, {"url": url})
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, url)

    def test_unlock_navigation(self):
        resp = self.request_lua("""
        function main(splash)
            splash:go(splash.args.url)
            splash:lock_navigation()
            splash:unlock_navigation()
            splash:wait(0.3)
            return splash:url()
        end
        """, {"url": self.mockurl("jsredirect")})
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, self.mockurl("jsredirect-target"))

    def test_go_navigation_locked(self):
        resp = self.request_lua("""
        function main(splash)
            splash:lock_navigation()
            local ok, reason = splash:go(splash.args.url)
            return {ok=ok, reason=reason}
        end
        """, {"url": self.mockurl("jsredirect"), "timeout": 1.0})
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {"reason": "navigation_locked"})



class SetContentTest(BaseLuaRenderTest):
    def test_set_content(self):
        resp = self.request_lua("""
        function main(splash)
            assert(splash:set_content("<html><head></head><body><h1>Hello</h1></body></html>"))
            return {
                html = splash:html(),
                url = splash:url(),
            }
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.json(), {
            "html": "<html><head></head><body><h1>Hello</h1></body></html>",
            "url": "about:blank",
        })

    def test_unicode(self):
        resp = self.request_lua("""
        function main(splash)
            assert(splash:set_content("проверка"))
            return splash:html()
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertIn(u'проверка', resp.text)

    def test_related_resources(self):
        script = """
        function main(splash)
            splash:set_content{
                data = [[
                    <html><body>
                        <img width=50 heigth=50 src="/slow.gif?n=0.2">
                    </body></html>
                ]],
                baseurl = splash.args.base,
            }
            return splash:png()
        end
        """
        resp = self.request_lua(script, {"base": self.mockurl("")})
        self.assertStatusCode(resp, 200)
        img = Image.open(StringIO(resp.content))
        self.assertEqual((0,0,0,255), img.getpixel((10, 10)))

        # the same, but with a bad base URL
        resp = self.request_lua(script, {"base": ""})
        self.assertStatusCode(resp, 200)
        img = Image.open(StringIO(resp.content))
        self.assertNotEqual((0,0,0,255), img.getpixel((10, 10)))

    def test_url(self):
        resp = self.request_lua("""
        function main(splash)
            splash:set_content{"hey", baseurl="http://example.com/foo"}
            return splash:url()
        end
        """)
        self.assertStatusCode(resp, 200)
        self.assertEqual(resp.text, "http://example.com/foo")
