import unittest
from utils.provisioning_input import parse_claim_input


class ClaimInputTests(unittest.TestCase):
    def test_literal_values_and_explicit_arrays(self):
        self.assertEqual(parse_claim_input("staff,member"), "staff,member")
        self.assertEqual(parse_claim_input(""), "")
        self.assertEqual(parse_claim_input('["staff", "member"]'), ["staff", "member"])
        self.assertEqual(parse_claim_input("[]"), [])

    def test_bad_array_is_an_error_not_a_false_match(self):
        for value in ("[staff]", "[1]", "[null]", "[[1]]"):
            with self.assertRaises(ValueError):
                parse_claim_input(value)


class DialogTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_nicegui_buttons_send_claims_and_render_results(self):
        import ast
        from pathlib import Path
        from unittest.mock import AsyncMock, patch
        from nicegui import ui
        async def click(button):
            wrapper=next(e.handler for e in button._event_listeners.values() if e.type=="click")
            with patch("nicegui.elements.button.handle_event", side_effect=lambda handler, event: handler()):
                await wrapper(None)
        tree = ast.parse((Path(__file__).parents[1] / "pages/admin.py").read_text())
        request = AsyncMock(return_value={"summary":["No account changes."],"rules":[]})
        scope = {"ui":ui,"attributes_get":lambda:[{"name":"role"}],"get_user_data":lambda:{"realm":"example.org"},
                 "parse_claim_input":parse_claim_input,"_provisioning_request":request,
                 "CONDITION_OPTIONS":{"equals":"Equals"}}
        functions=[n for n in tree.body if getattr(n,"name",None) in {"test_all_rules_dialog","test_rules_dialog"}]
        exec(compile(ast.Module(body=functions,type_ignores=[]),"pages/admin.py","exec"),scope)
        with ui.column() as host:
            client=ui.context.client
            first=set(client.elements)
            scope["test_all_rules_dialog"]()
            elements=[v for k,v in client.elements.items() if k not in first]
            attribute=next(e for e in elements if e._props.get("label")=="Attribute")
            attribute.set_value("role")
            value=next(e for e in elements if e._props.get("label")=="Value")
            value.set_value('["staff", "member"]')
            button=next(e for e in elements if isinstance(e,ui.button) and e.text=="Simulate")
            await click(button)
            self.assertEqual(request.call_args.args[1]["attributes"],{"role":["staff","member"]})
            self.assertEqual(request.call_args.args[1]["realm"],"example.org")
            self.assertTrue(button.enabled)
            self.assertTrue(any(isinstance(e,ui.label) and e.text=="No account changes." for e in client.elements.values()))
            # A transport failure produces no false success and releases the button.
            request.return_value=None
            await click(button)
            self.assertTrue(button.enabled)
            request.return_value={"matched":True}
            first=set(client.elements)
            scope["test_rules_dialog"]([{"id":7,"name":"Staff","attribute_name":"role","attribute_condition":"EQUALS","attribute_value":"staff"}])
            elements=[v for k,v in client.elements.items() if k not in first]
            next(e for e in elements if e._props.get("label")=="Value for role").set_value("staff")
            button=next(e for e in elements if isinstance(e,ui.button) and e.text=="Test")
            await click(button)
            self.assertEqual(request.call_args.args,("7/match",{"value":"staff"}))
            self.assertTrue(any(isinstance(e,ui.label) and e.text=="Match" for e in client.elements.values()))
        host.delete()
