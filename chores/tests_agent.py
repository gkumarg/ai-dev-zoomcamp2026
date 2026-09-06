"""Agent-loop tests. No Ollama, no network — everything goes through FakeClient.

The loop's only dependency on the outside world is the injected client, so a
canned transcript of `/api/chat` replies is enough to exercise every branch:
tool dispatch, refusals, the iteration cap, and transport failure.
"""

import copy
import json
import urllib.error

from django.test import TestCase
from django.test.utils import override_settings

from .agent import runner
from .agent.runner import AgentResult, OllamaClient, TransportError, run_assignment
from .agent.schemas import TOOLS
from .models import Chore, Person


def reply(content="", tool_calls=None):
    """One `/api/chat` response body, in Ollama's shape."""
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"message": message}


def call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


class FakeClient:
    """Replays canned replies and records what the loop sent it.

    Messages are deep-copied on the way in: the loop appends to one list as it
    goes, so recording it by reference would give every turn the final state.
    """

    def __init__(self, replies, raises=None):
        self.replies = list(replies)
        self.raises = raises
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        if self.raises is not None:
            raise self.raises
        if not self.replies:
            # A transcript that runs dry means the test under-specified the
            # model's behaviour; say so instead of failing somewhere obscure.
            raise AssertionError("FakeClient ran out of canned replies")
        return self.replies.pop(0)

    @property
    def last_messages(self):
        return self.calls[-1]["messages"]


class HouseholdTestCase(TestCase):
    """Alex is well ahead on effort; Sam and Jo are not. Two chores pending."""

    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.sam = Person.objects.create(name="Sam")
        done = Chore.objects.create(name="Bathroom", effort=5, assigned_to=self.alex)
        done.mark_complete()
        self.dishes = Chore.objects.create(name="Dishes", effort=2)
        self.vacuum = Chore.objects.create(name="Vacuum", effort=3)


class HappyPathTests(HouseholdTestCase):
    def setUp(self):
        super().setUp()
        self.client_stub = FakeClient(
            [
                reply(tool_calls=[call("get_people", {}), call("get_pending_chores", {})]),
                reply(
                    tool_calls=[
                        call("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id}),
                        call("assign_chore", {"chore_id": self.vacuum.id, "person_id": self.sam.id}),
                    ]
                ),
                reply(content="Sam takes both; Alex already did the bathroom."),
            ]
        )
        self.result = run_assignment(client=self.client_stub)

    def test_run_succeeded(self):
        self.assertTrue(self.result.ok)
        self.assertEqual(self.result.error, "")

    def test_every_pending_chore_was_assigned(self):
        self.dishes.refresh_from_db()
        self.vacuum.refresh_from_db()
        self.assertEqual(self.dishes.assigned_to, self.sam)
        self.assertEqual(self.vacuum.assigned_to, self.sam)
        self.assertEqual(self.dishes.status, Chore.Status.ASSIGNED)
        self.assertEqual(self.result.unassigned, [])

    def test_assignments_carry_names_for_the_page(self):
        self.assertEqual(
            [(a.chore_name, a.person_name) for a in self.result.assignments],
            [("Dishes", "Sam"), ("Vacuum", "Sam")],
        )

    def test_reasoning_text_is_captured(self):
        self.assertEqual(
            self.result.reasoning, "Sam takes both; Alex already did the bathroom."
        )

    def test_diagnostics(self):
        self.assertEqual(self.result.iterations, 3)
        self.assertEqual(len(self.result.tool_calls), 4)
        self.assertEqual(self.result.refusals, [])
        self.assertTrue(all(tc.ok for tc in self.result.tool_calls))

    def test_schemas_are_sent_every_turn(self):
        for sent in self.client_stub.calls:
            self.assertIs(sent["tools"], TOOLS)


class MessageShapeTests(HouseholdTestCase):
    """The tool result has to actually reach the model, in Ollama's shape."""

    def setUp(self):
        super().setUp()
        self.client_stub = FakeClient(
            [
                reply(content="Looking.", tool_calls=[call("get_people", {})]),
                reply(content="Done."),
            ]
        )
        self.result = run_assignment(client=self.client_stub)

    def test_first_turn_is_system_then_user(self):
        first = self.client_stub.calls[0]["messages"]
        self.assertEqual([m["role"] for m in first], ["system", "user"])
        self.assertIn("CUMULATIVE EFFORT", first[0]["content"])

    def test_second_turn_replays_assistant_then_tool_result(self):
        second = self.client_stub.last_messages
        self.assertEqual(
            [m["role"] for m in second], ["system", "user", "assistant", "tool"]
        )
        assistant = second[2]
        self.assertEqual(assistant["content"], "Looking.")
        self.assertEqual(assistant["tool_calls"], [call("get_people", {})])

    def test_tool_result_content_is_the_json_the_tool_returned(self):
        tool_message = self.client_stub.last_messages[-1]
        self.assertEqual(tool_message["name"], "get_people")
        payload = json.loads(tool_message["content"])
        self.assertEqual(
            {person["name"] for person in payload}, {"Alex", "Sam"}
        )
        alex = next(p for p in payload if p["name"] == "Alex")
        self.assertEqual(alex["effort_total"], 5)


class RefusalTests(HouseholdTestCase):
    def test_hallucinated_chore_id_is_fed_back_and_the_loop_continues(self):
        client_stub = FakeClient(
            [
                reply(tool_calls=[call("assign_chore", {"chore_id": 99999, "person_id": self.sam.id})]),
                reply(
                    tool_calls=[
                        call("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id})
                    ]
                ),
                reply(content="Corrected the id and assigned it."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.refusals), 1)
        self.assertIn("99999", result.refusals[0])
        # The refusal reached the model rather than ending the run...
        refusal_message = client_stub.calls[1]["messages"][-1]
        self.assertEqual(refusal_message["role"], "tool")
        self.assertIn("99999", json.loads(refusal_message["content"])["error"])
        # ...and the retry landed.
        self.assertEqual([a.chore_name for a in result.assignments], ["Dishes"])

    def test_hallucinated_person_id_is_refused(self):
        client_stub = FakeClient(
            [
                reply(tool_calls=[call("assign_chore", {"chore_id": self.dishes.id, "person_id": 4242})]),
                reply(content="Gave up on that one."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertTrue(result.ok)
        self.assertIn("4242", result.refusals[0])
        self.assertEqual(result.assignments, [])
        self.dishes.refresh_from_db()
        self.assertEqual(self.dishes.status, Chore.Status.PENDING)

    def test_unknown_tool_name_is_refused_like_a_bad_argument(self):
        client_stub = FakeClient(
            [
                reply(tool_calls=[call("delete_everything", {"confirm": True})]),
                reply(content="Sorry."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.refusals), 1)
        self.assertIn("delete_everything", result.refusals[0])
        # The valid names are listed, so the model can pick a real one.
        self.assertIn("assign_chore", result.refusals[0])
        self.assertFalse(result.tool_calls[0].ok)

    def test_missing_arguments_are_refused_not_raised(self):
        client_stub = FakeClient(
            [
                reply(tool_calls=[call("assign_chore", {})]),
                reply(content="Never mind."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.refusals), 1)

    def test_unparseable_argument_string_is_refused(self):
        client_stub = FakeClient(
            [
                reply(tool_calls=[call("assign_chore", "not json at all")]),
                reply(content="Never mind."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertTrue(result.ok)
        self.assertIn("must be a JSON object", result.refusals[0])

    def test_a_real_bug_in_a_tool_is_not_swallowed(self):
        # Only ToolError means "the model got it wrong"; anything else is ours.
        def exploding_tool(arguments):
            raise ValueError("boom")

        client_stub = FakeClient([reply(tool_calls=[call("get_people", {})])])
        original = runner.TOOL_REGISTRY["get_people"]
        runner.TOOL_REGISTRY["get_people"] = exploding_tool
        try:
            with self.assertRaises(ValueError):
                run_assignment(client=client_stub)
        finally:
            runner.TOOL_REGISTRY["get_people"] = original


class ArgumentFormatTests(HouseholdTestCase):
    def test_arguments_as_a_json_string_still_work(self):
        client_stub = FakeClient(
            [
                reply(
                    tool_calls=[
                        call(
                            "assign_chore",
                            json.dumps({"chore_id": self.dishes.id, "person_id": self.sam.id}),
                        )
                    ]
                ),
                reply(content="Assigned."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertEqual([a.chore_name for a in result.assignments], ["Dishes"])
        self.dishes.refresh_from_db()
        self.assertEqual(self.dishes.assigned_to, self.sam)

    def test_ids_as_strings_still_work(self):
        client_stub = FakeClient(
            [
                reply(
                    tool_calls=[
                        call(
                            "assign_chore",
                            {"chore_id": str(self.dishes.id), "person_id": str(self.sam.id)},
                        )
                    ]
                ),
                reply(content="Assigned."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertEqual(len(result.assignments), 1)

    def test_several_tool_calls_in_one_message_all_execute(self):
        client_stub = FakeClient(
            [
                reply(
                    tool_calls=[
                        call("get_people", {}),
                        call("get_pending_chores", {}),
                        call("get_history", {"person_id": self.alex.id}),
                        call("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id}),
                    ]
                ),
                reply(content="All four ran."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertEqual(
            [tc.name for tc in result.tool_calls],
            ["get_people", "get_pending_chores", "get_history", "assign_chore"],
        )
        # One tool message per call, in order, all in the same follow-up turn.
        tool_messages = [m for m in client_stub.last_messages if m["role"] == "tool"]
        self.assertEqual(len(tool_messages), 4)
        self.assertEqual(result.iterations, 2)

    def test_missing_arguments_key_is_treated_as_empty(self):
        client_stub = FakeClient(
            [
                reply(tool_calls=[{"function": {"name": "get_people"}}]),
                reply(content="Fine."),
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertTrue(result.tool_calls[0].ok)


class IterationCapTests(HouseholdTestCase):
    def test_a_model_that_never_stops_is_cut_off_with_partial_results(self):
        # Assigns one chore, then asks for the same read forever.
        replies = [
            reply(
                tool_calls=[
                    call("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id})
                ]
            )
        ] + [reply(tool_calls=[call("get_people", {})]) for _ in range(20)]
        client_stub = FakeClient(replies)

        result = run_assignment(client=client_stub, max_iterations=5)

        self.assertTrue(result.ok)
        self.assertEqual(result.iterations, 5)
        self.assertEqual(len(client_stub.calls), 5)
        self.assertEqual([a.chore_name for a in result.assignments], ["Dishes"])
        self.assertEqual(result.unassigned, ["Vacuum"])
        self.assertIn("without finishing", result.message)

    @override_settings(OLLAMA_MAX_ITERATIONS=2)
    def test_the_cap_defaults_to_the_setting(self):
        client_stub = FakeClient([reply(tool_calls=[call("get_people", {})]) for _ in range(10)])

        result = run_assignment(client=client_stub)

        self.assertEqual(len(client_stub.calls), 2)
        self.assertEqual(result.iterations, 2)


class TransportFailureTests(HouseholdTestCase):
    def assert_failed(self, result, fragment):
        self.assertIsInstance(result, AgentResult)
        self.assertFalse(result.ok)
        self.assertIn(fragment, result.error)
        self.assertIn(fragment, result.message)
        self.assertEqual(result.assignments, [])
        # The page still needs to know what is outstanding.
        self.assertEqual(sorted(result.unassigned), ["Dishes", "Vacuum"])

    def test_unreachable_ollama_returns_a_failure_result(self):
        client_stub = FakeClient([], raises=TransportError("Could not reach Ollama at x"))

        self.assert_failed(run_assignment(client=client_stub), "Could not reach Ollama")

    def test_partial_assignments_survive_a_mid_run_failure(self):
        class FlakyClient(FakeClient):
            def chat(self, messages, tools):
                if self.calls:
                    self.calls.append({"messages": [], "tools": tools})
                    raise TransportError("connection reset")
                return super().chat(messages, tools)

        client_stub = FlakyClient(
            [
                reply(
                    tool_calls=[
                        call("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id})
                    ]
                )
            ]
        )

        result = run_assignment(client=client_stub)

        self.assertFalse(result.ok)
        self.assertEqual([a.chore_name for a in result.assignments], ["Dishes"])
        self.assertIn("kept", result.message)

    def test_a_reply_without_a_message_is_a_failure_not_a_crash(self):
        client_stub = FakeClient([{"error": "model not found"}])

        result = run_assignment(client=client_stub)

        self.assertFalse(result.ok)
        self.assertIn("did not contain a message", result.error)


class OllamaClientTests(TestCase):
    """The real client, with `urlopen` injected — still zero network calls."""

    class FakeResponse:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_request_shape(self):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            seen["body"] = json.loads(request.data)
            seen["content_type"] = request.get_header("Content-type")
            return self.FakeResponse(json.dumps(reply(content="hi")).encode())

        client = OllamaClient(
            host="http://ollama.test:11434/", model="test-model", timeout=7, urlopen=fake_urlopen
        )
        response = client.chat(messages=[{"role": "user", "content": "go"}], tools=TOOLS)

        self.assertEqual(seen["url"], "http://ollama.test:11434/api/chat")
        self.assertEqual(seen["timeout"], 7)
        self.assertEqual(seen["content_type"], "application/json")
        self.assertEqual(seen["body"]["model"], "test-model")
        self.assertFalse(seen["body"]["stream"])
        self.assertEqual(seen["body"]["tools"], TOOLS)
        self.assertEqual(response["message"]["content"], "hi")

    @override_settings(
        OLLAMA_HOST="http://configured:1234", OLLAMA_MODEL="configured-model", OLLAMA_TIMEOUT=3
    )
    def test_defaults_come_from_settings(self):
        client = OllamaClient()

        self.assertEqual(client.host, "http://configured:1234")
        self.assertEqual(client.model, "configured-model")
        self.assertEqual(client.timeout, 3)

    def _client_raising(self, exc):
        def fake_urlopen(request, timeout=None):
            raise exc

        return OllamaClient(urlopen=fake_urlopen)

    def test_connection_refused_becomes_transport_error(self):
        client = self._client_raising(urllib.error.URLError(ConnectionRefusedError(111, "refused")))

        with self.assertRaises(TransportError) as ctx:
            client.chat(messages=[], tools=[])
        self.assertIn("ollama serve", str(ctx.exception))

    def test_timeout_becomes_transport_error(self):
        client = self._client_raising(TimeoutError("timed out"))

        with self.assertRaises(TransportError):
            client.chat(messages=[], tools=[])

    def test_http_error_becomes_transport_error(self):
        client = self._client_raising(
            urllib.error.HTTPError("http://x/api/chat", 500, "Server Error", {}, None)
        )

        with self.assertRaises(TransportError) as ctx:
            client.chat(messages=[], tools=[])
        self.assertIn("500", str(ctx.exception))

    def test_non_json_body_becomes_transport_error(self):
        def fake_urlopen(request, timeout=None):
            return self.FakeResponse(b"<html>proxy error</html>")

        client = OllamaClient(urlopen=fake_urlopen)

        with self.assertRaises(TransportError) as ctx:
            client.chat(messages=[], tools=[])
        self.assertIn("not JSON", str(ctx.exception))

    def test_transport_failures_reach_the_loop_as_a_result(self):
        # End to end with the real client: an unreachable host must not raise.
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

        Person.objects.create(name="Sam")
        Chore.objects.create(name="Dishes", effort=2)

        result = run_assignment(client=OllamaClient(urlopen=fake_urlopen))

        self.assertFalse(result.ok)
        self.assertIn("Could not reach Ollama", result.error)


class EmptyStateTests(TestCase):
    """Nothing to do must be answered instantly — not after 30s of inference."""

    def test_no_people_returns_without_calling_the_model(self):
        Chore.objects.create(name="Dishes", effort=2)
        client_stub = FakeClient([])

        result = run_assignment(client=client_stub)

        self.assertEqual(client_stub.calls, [])
        self.assertTrue(result.ok)
        self.assertEqual(result.assignments, [])
        self.assertIn("nobody on the roster", result.message)

    def test_no_pending_chores_returns_without_calling_the_model(self):
        person = Person.objects.create(name="Sam")
        Chore.objects.create(
            name="Dishes", effort=2, assigned_to=person, status=Chore.Status.ASSIGNED
        )
        client_stub = FakeClient([])

        result = run_assignment(client=client_stub)

        self.assertEqual(client_stub.calls, [])
        self.assertTrue(result.ok)
        self.assertIn("No chores are pending", result.message)

    def test_empty_database_returns_without_calling_the_model(self):
        client_stub = FakeClient([])

        result = run_assignment(client=client_stub)

        self.assertEqual(client_stub.calls, [])
        self.assertTrue(result.ok)


class RegistryTests(TestCase):
    def test_every_advertised_tool_can_be_dispatched(self):
        advertised = {schema["function"]["name"] for schema in TOOLS}

        self.assertEqual(advertised, set(runner.TOOL_REGISTRY))
