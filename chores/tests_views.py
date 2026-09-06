from unittest import mock

from django.test import TestCase
from django.urls import reverse

from .agent.runner import AgentResult, Assignment, ToolCall
from .models import Chore, History, Person


class PageRenderTests(TestCase):
    """Every nav destination returns 200 with the expected template."""

    def test_root_redirects_to_chore_list(self):
        response = self.client.get("/")
        self.assertRedirects(response, reverse("chores:chore_list"))

    def test_person_list_page(self):
        response = self.client.get(reverse("chores:person_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/person_list.html")

    def test_chore_list_page(self):
        response = self.client.get(reverse("chores:chore_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/chore_list.html")

    def test_assign_page(self):
        # The stub is gone (task 10); this now exercises the real page.
        # A dedicated `AssignViewTests` class below covers the agent-call path.
        response = self.client.get(reverse("chores:assign"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/assign.html")

    def test_status_page(self):
        response = self.client.get(reverse("chores:status"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/status.html")

    def test_empty_states_render_without_error(self):
        # Nothing in the DB at all — every list page should still render sensibly.
        for url_name in ("chores:person_list", "chores:chore_list", "chores:status"):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200)

    def test_person_remove_confirm_page(self):
        alex = Person.objects.create(name="Alex")
        response = self.client.get(reverse("chores:person_remove_confirm", args=[alex.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/person_confirm_remove.html")


class PersonViewTests(TestCase):
    def test_add_person(self):
        response = self.client.post(reverse("chores:person_add"), {"name": "Alex"})
        self.assertRedirects(response, reverse("chores:person_list"))
        self.assertTrue(Person.objects.filter(name="Alex").exists())

    def test_add_person_get_not_allowed(self):
        response = self.client.get(reverse("chores:person_add"))
        self.assertEqual(response.status_code, 405)

    def test_duplicate_person_name_is_a_form_error_not_a_500(self):
        Person.objects.create(name="Alex")

        response = self.client.post(reverse("chores:person_add"), {"name": "Alex"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/person_list.html")
        self.assertFalse(response.context["form"].is_valid())
        self.assertIn("name", response.context["form"].errors)
        self.assertEqual(Person.objects.filter(name="Alex").count(), 1)

    def test_person_list_shows_effort_totals(self):
        alex = Person.objects.create(name="Alex")
        Chore.objects.create(name="Dishes", effort=3, assigned_to=alex).mark_complete()

        response = self.client.get(reverse("chores:person_list"))

        people_by_name = {p.name: p.effort_total for p in response.context["people"]}
        self.assertEqual(people_by_name["Alex"], 3)

    def test_remove_person_get_not_allowed(self):
        alex = Person.objects.create(name="Alex")
        response = self.client.get(reverse("chores:person_remove", args=[alex.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Person.objects.filter(pk=alex.pk).exists())

    def test_remove_person_deletes_them(self):
        alex = Person.objects.create(name="Alex")
        response = self.client.post(reverse("chores:person_remove", args=[alex.pk]))
        self.assertRedirects(response, reverse("chores:person_list"))
        self.assertFalse(Person.objects.filter(pk=alex.pk).exists())

    def test_removing_person_resets_their_assigned_chores_to_pending(self):
        alex = Person.objects.create(name="Alex")
        assigned_chore = Chore.objects.create(
            name="Vacuum", effort=2, status=Chore.Status.ASSIGNED, assigned_to=alex
        )
        done_chore = Chore.objects.create(name="Dishes", effort=1, assigned_to=alex)
        done_chore.mark_complete()

        self.client.post(reverse("chores:person_remove", args=[alex.pk]))

        assigned_chore.refresh_from_db()
        done_chore.refresh_from_db()
        self.assertEqual(assigned_chore.status, Chore.Status.PENDING)
        self.assertIsNone(assigned_chore.assigned_to)
        # A chore already marked done should be left alone — only the
        # stranded "assigned" chore needs to go back to the pool.
        self.assertEqual(done_chore.status, Chore.Status.DONE)


class ChoreViewTests(TestCase):
    def test_add_chore(self):
        response = self.client.post(
            reverse("chores:chore_add"), {"name": "Vacuum", "effort": 3}
        )
        self.assertRedirects(response, reverse("chores:chore_list"))
        self.assertTrue(Chore.objects.filter(name="Vacuum", effort=3).exists())

    def test_add_chore_get_not_allowed(self):
        response = self.client.get(reverse("chores:chore_add"))
        self.assertEqual(response.status_code, 405)

    def test_effort_out_of_range_is_a_form_error_not_a_500(self):
        response = self.client.post(
            reverse("chores:chore_add"), {"name": "Vacuum", "effort": 9}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/chore_list.html")
        self.assertFalse(response.context["form"].is_valid())
        self.assertIn("effort", response.context["form"].errors)
        self.assertFalse(Chore.objects.filter(name="Vacuum").exists())

    def test_effort_zero_is_also_rejected(self):
        response = self.client.post(
            reverse("chores:chore_add"), {"name": "Vacuum", "effort": 0}
        )
        self.assertIn("effort", response.context["form"].errors)

    def test_chore_list_filters_by_status(self):
        Chore.objects.create(name="Pending chore", effort=1)
        done = Chore.objects.create(name="Done chore", effort=1)
        Person.objects.create(name="Alex")
        done.mark_complete(person=Person.objects.get(name="Alex"))

        response = self.client.get(reverse("chores:chore_list"), {"status": "done"})

        names = [c.name for c in response.context["chores"]]
        self.assertEqual(names, ["Done chore"])


class CompleteChoreViewTests(TestCase):
    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.chore = Chore.objects.create(name="Dishes", effort=3, assigned_to=self.alex)

    def test_complete_get_not_allowed(self):
        response = self.client.get(reverse("chores:chore_complete", args=[self.chore.pk]))
        self.assertEqual(response.status_code, 405)

    def test_mark_complete_writes_history_and_redirects(self):
        response = self.client.post(reverse("chores:chore_complete", args=[self.chore.pk]))

        self.assertRedirects(response, reverse("chores:chore_list"))
        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.DONE)
        self.assertEqual(History.objects.count(), 1)

    def test_completing_twice_shows_error_message_instead_of_500(self):
        self.client.post(reverse("chores:chore_complete", args=[self.chore.pk]))

        response = self.client.post(
            reverse("chores:chore_complete", args=[self.chore.pk]), follow=True
        )

        self.assertEqual(response.status_code, 200)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("already done" in m for m in messages))
        self.assertEqual(History.objects.count(), 1)

    def test_completing_unassigned_chore_shows_error_message(self):
        unassigned = Chore.objects.create(name="Bins", effort=2)

        response = self.client.post(
            reverse("chores:chore_complete", args=[unassigned.pk]), follow=True
        )

        self.assertEqual(response.status_code, 200)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("nobody" in m for m in messages))
        self.assertEqual(History.objects.count(), 0)


class StatusViewTests(TestCase):
    def test_status_shows_correct_totals_sorted_heaviest_first(self):
        alex = Person.objects.create(name="Alex")
        sam = Person.objects.create(name="Sam")
        Chore.objects.create(name="Bins", effort=5, assigned_to=sam).mark_complete()
        Chore.objects.create(name="Dishes", effort=2, assigned_to=alex).mark_complete()

        response = self.client.get(reverse("chores:status"))

        people = list(response.context["people"])
        self.assertEqual([p.name for p in people], ["Sam", "Alex"])
        self.assertEqual([p.effort_total for p in people], [5, 2])

    def test_status_page_with_no_people(self):
        response = self.client.get(reverse("chores:status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["people"]), [])


class AssignViewTests(TestCase):
    """`chores:assign` — task 10.

    `run_assignment` is patched as `chores.views.run_assignment` (the name
    the view module imported it under), so none of these tests ever touch
    Ollama or the network: GET never even calls it, and POST calls the fake
    in its place.
    """

    def test_get_renders_button_and_does_not_call_the_agent(self):
        Person.objects.create(name="Alex")
        Chore.objects.create(name="Dishes", effort=3)

        with mock.patch("chores.views.run_assignment") as fake_run:
            response = self.client.get(reverse("chores:assign"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/assign.html")
        fake_run.assert_not_called()
        self.assertContains(response, "Assign Chores")
        self.assertIsNone(response.context["result"])
        self.assertContains(response, "Dishes")

    def test_post_renders_assignments_and_reasoning(self):
        alex = Person.objects.create(name="Alex")
        sam = Person.objects.create(name="Sam")
        Chore.objects.create(name="Dishes", effort=3)
        Chore.objects.create(name="Bins", effort=2)

        fake_result = AgentResult(
            ok=True,
            message="Assigned 2 chore(s) — nothing left pending.",
            assignments=[
                Assignment(chore_id=1, chore_name="Dishes", person_id=alex.pk, person_name="Alex"),
                Assignment(chore_id=2, chore_name="Bins", person_id=sam.pk, person_name="Sam"),
            ],
            reasoning="Alex and Sam had equal history, so I split the two chores between them.",
            iterations=2,
        )

        with mock.patch("chores.views.run_assignment", return_value=fake_result) as fake_run:
            response = self.client.post(reverse("chores:assign"))

        self.assertEqual(response.status_code, 200)
        fake_run.assert_called_once_with()
        self.assertContains(response, "Dishes")
        self.assertContains(response, "Alex")
        self.assertContains(response, "Bins")
        self.assertContains(response, "Sam")
        self.assertContains(
            response, "Alex and Sam had equal history, so I split the two chores between them."
        )
        self.assertContains(response, "Assigned 2 chore(s) — nothing left pending.")

    def test_post_with_failed_run_renders_error_state_not_500(self):
        Person.objects.create(name="Alex")
        Chore.objects.create(name="Dishes", effort=3)

        fake_result = AgentResult(
            ok=False,
            message="The agent could not finish: Could not reach Ollama at http://x (boom).",
            error="Could not reach Ollama at http://x (boom).",
            unassigned=["Dishes"],
        )

        with mock.patch("chores.views.run_assignment", return_value=fake_result):
            response = self.client.post(reverse("chores:assign"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The agent could not finish")
        self.assertContains(response, "class=\"agent-message error\"")
        # The error detail is already inside `message` — it should not appear
        # a second time in a separate rendered block.
        self.assertEqual(
            response.content.decode().count("Could not reach Ollama at http://x (boom)."),
            1,
        )

    def test_post_with_no_people_shows_friendly_message_without_error(self):
        Chore.objects.create(name="Dishes", effort=3)

        fake_result = AgentResult(
            ok=True,
            message="There is nobody on the roster yet, so there is nothing to assign.",
        )

        with mock.patch("chores.views.run_assignment", return_value=fake_result):
            response = self.client.post(reverse("chores:assign"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "There is nobody on the roster yet, so there is nothing to assign."
        )
        self.assertNotContains(response, "class=\"agent-message error\"")

    def test_post_with_no_pending_chores_shows_friendly_message(self):
        Person.objects.create(name="Alex")

        fake_result = AgentResult(
            ok=True, message="No chores are pending, so there is nothing to assign."
        )

        with mock.patch("chores.views.run_assignment", return_value=fake_result):
            response = self.client.post(reverse("chores:assign"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "No chores are pending, so there is nothing to assign."
        )

    def test_diagnostics_block_renders_tool_calls_and_refusals(self):
        Person.objects.create(name="Alex")
        Chore.objects.create(name="Dishes", effort=3)

        fake_result = AgentResult(
            ok=True,
            message="Assigned 1 chore(s) — nothing left pending.",
            iterations=3,
            tool_calls=[
                ToolCall(name="get_people", arguments={}, ok=True, result=[{"id": 1}]),
                ToolCall(
                    name="assign_chore",
                    arguments={"chore_id": 99, "person_id": 1},
                    ok=False,
                    error="There is no chore with id 99.",
                ),
            ],
            refusals=["There is no chore with id 99."],
        )

        with mock.patch("chores.views.run_assignment", return_value=fake_result):
            response = self.client.post(reverse("chores:assign"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<details")
        self.assertContains(response, "get_people")
        self.assertContains(response, "assign_chore")
        self.assertContains(response, "There is no chore with id 99.")

    def test_effort_totals_shown_after_a_run(self):
        alex = Person.objects.create(name="Alex")
        Chore.objects.create(name="Vacuum", effort=4, assigned_to=alex).mark_complete()
        Chore.objects.create(name="Dishes", effort=3)

        fake_result = AgentResult(ok=True, message="Assigned 1 chore(s) — nothing left pending.")

        with mock.patch("chores.views.run_assignment", return_value=fake_result):
            response = self.client.post(reverse("chores:assign"))

        people_by_name = {p.name: p.effort_total for p in response.context["people"]}
        self.assertEqual(people_by_name["Alex"], 4)


class ChoreListFilterTests(TestCase):
    def test_an_unrecognised_status_filter_falls_back_to_showing_everything(self):
        # A hand-edited or stale query string must not silently show an empty
        # list, which reads as "you have no chores".
        Chore.objects.create(name="Pending chore", effort=1)
        Chore.objects.create(name="Another chore", effort=2)

        response = self.client.get(reverse("chores:chore_list"), {"status": "bogus"})

        self.assertEqual(len(response.context["chores"]), 2)

    def test_an_empty_status_filter_shows_everything(self):
        Chore.objects.create(name="Pending chore", effort=1)

        response = self.client.get(reverse("chores:chore_list"), {"status": ""})

        self.assertEqual(len(response.context["chores"]), 1)

    def test_filtering_to_a_status_with_no_chores_is_not_an_error(self):
        Chore.objects.create(name="Pending chore", effort=1)

        response = self.client.get(reverse("chores:chore_list"), {"status": "done"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["chores"]), 0)
