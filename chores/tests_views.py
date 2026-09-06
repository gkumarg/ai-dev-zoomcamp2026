from django.test import TestCase
from django.urls import reverse

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

    def test_assign_stub_page(self):
        response = self.client.get(reverse("chores:assign"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chores/assign_stub.html")

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
