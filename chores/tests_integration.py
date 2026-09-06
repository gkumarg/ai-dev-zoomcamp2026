"""End-to-end tests through the real stack.

Everything else fakes at a seam: the view tests replace `run_assignment`
wholesale, and the agent tests replace the tool layer's caller. That leaves a
gap — nothing checks that the view, the loop, the tool functions and the
database actually fit together. A renamed `AgentResult` field or a tool
missing from `TOOL_REGISTRY` would pass every unit test and still break the
page.

So these tests fake exactly one thing: the HTTP call to Ollama. Everything
downstream of it is the real code, and the assertions are on the database and
the rendered page.
"""

import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from .agent.runner import TransportError, run_assignment
from .models import Chore, History, Person


class FakeOllama:
    """Replays canned `/api/chat` responses and records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.received = []

    def chat(self, messages, tools):
        self.received.append({"messages": list(messages), "tools": tools})
        if not self.replies:
            raise AssertionError("The loop asked for more turns than were scripted.")
        return self.replies.pop(0)


def says(content="", tool_calls=None):
    """One assistant turn, in Ollama's response shape."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"function": {"name": name, "arguments": arguments}}
            for name, arguments in tool_calls
        ]
    return {"message": message}


class AssignEndToEndTests(TestCase):
    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.sam = Person.objects.create(name="Sam")
        # Alex is already well ahead, so a fair run loads up Sam.
        heavy = Chore.objects.create(name="Deep clean the oven", effort=5, assigned_to=self.alex)
        heavy.mark_complete()

        self.dishes = Chore.objects.create(name="Dishes", effort=2)
        self.bins = Chore.objects.create(name="Bins", effort=1)

    def post_assign(self, fake):
        """POST the assign page with only the Ollama transport faked."""
        with mock.patch(
            "chores.views.run_assignment", lambda: run_assignment(client=fake)
        ):
            return self.client.post(reverse("chores:assign"))

    def test_a_full_run_assigns_chores_in_the_database(self):
        fake = FakeOllama(
            says(tool_calls=[("get_people", {}), ("get_pending_chores", {})]),
            says(
                tool_calls=[
                    ("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id}),
                    ("assign_chore", {"chore_id": self.bins.id, "person_id": self.sam.id}),
                ]
            ),
            says("Alex has already done 5 effort, so both chores go to Sam."),
        )

        response = self.post_assign(fake)

        self.assertEqual(response.status_code, 200)
        self.dishes.refresh_from_db()
        self.bins.refresh_from_db()
        self.assertEqual(self.dishes.assigned_to, self.sam)
        self.assertEqual(self.bins.assigned_to, self.sam)
        self.assertEqual(self.dishes.status, Chore.Status.ASSIGNED)

    def test_the_page_shows_what_was_assigned_and_why(self):
        fake = FakeOllama(
            says(tool_calls=[("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id})]),
            says("Sam is furthest behind on effort."),
        )

        page = self.post_assign(fake).content.decode()

        self.assertIn("Dishes", page)
        self.assertIn("Sam", page)
        self.assertIn("Sam is furthest behind on effort.", page)

    def test_the_model_really_sees_the_current_state(self):
        # Proves the tool results reach the model rather than being computed
        # and dropped — the failure that would make the agent reason blind.
        fake = FakeOllama(
            says(tool_calls=[("get_people", {})]),
            says("Understood."),
        )

        self.post_assign(fake)

        tool_result = fake.received[1]["messages"][-1]
        self.assertEqual(tool_result["role"], "tool")
        people = json.loads(tool_result["content"])
        self.assertEqual(
            {person["name"]: person["effort_total"] for person in people},
            {"Alex": 5, "Sam": 0},
        )

    def test_every_advertised_tool_is_actually_callable(self):
        # The schemas the model is sent and the registry the loop dispatches
        # through are separate structures; this catches them drifting apart.
        fake = FakeOllama(
            says(
                tool_calls=[
                    ("get_people", {}),
                    ("get_pending_chores", {}),
                    ("get_history", {"person_id": self.alex.id}),
                    ("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id}),
                ]
            ),
            says("Done."),
        )

        self.post_assign(fake)

        results = [json.loads(m["content"]) for m in fake.received[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(results), 4)
        for result in results:
            self.assertNotIn("error", result if isinstance(result, dict) else {})

    def test_a_hallucinating_model_recovers_and_the_database_stays_clean(self):
        fake = FakeOllama(
            says(tool_calls=[("assign_chore", {"chore_id": 999999, "person_id": self.sam.id})]),
            says(tool_calls=[("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id})]),
            says("I had a stale id, then corrected it."),
        )

        response = self.post_assign(fake)

        self.dishes.refresh_from_db()
        self.assertEqual(self.dishes.assigned_to, self.sam)
        # The refusal reached the model, and nothing bogus was written.
        self.assertEqual(Chore.objects.filter(status=Chore.Status.ASSIGNED).count(), 1)
        self.assertContains(response, "999999")

    def test_a_model_that_reassigns_a_done_chore_is_refused(self):
        done = Chore.objects.get(name="Deep clean the oven")
        fake = FakeOllama(
            says(tool_calls=[("assign_chore", {"chore_id": done.id, "person_id": self.sam.id})]),
            says("Oh, that one was finished."),
        )

        self.post_assign(fake)

        done.refresh_from_db()
        self.assertEqual(done.status, Chore.Status.DONE)
        self.assertEqual(done.assigned_to, self.alex)
        # The completed effort is untouched, so fairness history is intact.
        self.assertEqual(self.alex.total_effort(), 5)

    def test_ollama_being_down_renders_an_error_and_changes_nothing(self):
        class Down:
            def chat(self, messages, tools):
                raise TransportError("Could not reach Ollama at http://localhost:11434.")

        response = self.post_assign(Down())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Could not reach Ollama")
        self.assertEqual(Chore.objects.filter(status=Chore.Status.PENDING).count(), 2)
        self.assertEqual(Chore.objects.filter(status=Chore.Status.ASSIGNED).count(), 0)

    def test_assigned_chores_can_then_be_completed_through_the_ui(self):
        # The whole loop: the agent assigns, a person completes, the fairness
        # totals move, and the status page reflects it.
        fake = FakeOllama(
            says(tool_calls=[("assign_chore", {"chore_id": self.dishes.id, "person_id": self.sam.id})]),
            says("Sam takes the dishes."),
        )
        self.post_assign(fake)

        self.client.post(reverse("chores:chore_complete", args=[self.dishes.id]))

        self.assertEqual(History.objects.filter(person=self.sam).count(), 1)
        self.assertEqual(self.sam.total_effort(), 2)
        status_page = self.client.get(reverse("chores:status")).content.decode()
        self.assertIn("Sam", status_page)
