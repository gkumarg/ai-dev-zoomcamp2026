from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .models import Chore, History, Person


class SeedDemoTests(TestCase):
    """The demo data is what the app is graded and demoed on, so it has to be
    both correct and safe to re-run."""

    def seed(self, *args):
        out = StringIO()
        call_command("seed_demo", *args, stdout=out)
        return out.getvalue()

    def test_creates_people_chores_and_history(self):
        self.seed()

        self.assertEqual(Person.objects.count(), 3)
        self.assertTrue(Chore.objects.filter(status=Chore.Status.PENDING).exists())
        self.assertTrue(History.objects.exists())

    def test_history_is_lopsided_so_the_agent_has_something_to_balance(self):
        # If the starting totals were level, a round-robin would look just as
        # good as effort-balancing and the demo would prove nothing.
        self.seed()

        totals = {p.name: p.effort_total for p in Person.objects.with_effort_totals()}

        self.assertEqual(totals, {"Alex": 12, "Jo": 7, "Sam": 3})

    def test_completed_chores_are_consistent_with_their_history(self):
        # The command completes chores through mark_complete rather than
        # writing rows directly, so demo data cannot drift from real data.
        self.seed()

        for entry in History.objects.select_related("chore"):
            self.assertEqual(entry.chore.status, Chore.Status.DONE)
            self.assertEqual(entry.effort, entry.chore.effort)
            self.assertEqual(entry.chore.assigned_to, entry.person)

    def test_pending_chores_span_a_range_of_effort(self):
        self.seed()

        efforts = set(
            Chore.objects.filter(status=Chore.Status.PENDING).values_list("effort", flat=True)
        )

        self.assertGreater(len(efforts), 1)
        self.assertTrue(all(1 <= effort <= 5 for effort in efforts))

    def test_running_twice_does_not_duplicate_anything(self):
        self.seed()
        counts = (Person.objects.count(), Chore.objects.count(), History.objects.count())

        self.seed()

        self.assertEqual(
            (Person.objects.count(), Chore.objects.count(), History.objects.count()),
            counts,
        )

    def test_running_twice_does_not_double_count_effort(self):
        # The sharpest re-run risk: completing a seeded chore a second time
        # would inflate the totals the fairness logic depends on.
        self.seed()

        self.seed()

        totals = {p.name: p.effort_total for p in Person.objects.with_effort_totals()}
        self.assertEqual(totals, {"Alex": 12, "Jo": 7, "Sam": 3})

    def test_reset_clears_pre_existing_data(self):
        stranger = Person.objects.create(name="Someone Else")
        Chore.objects.create(name="Not a demo chore", effort=1, assigned_to=stranger)

        self.seed("--reset")

        self.assertFalse(Person.objects.filter(name="Someone Else").exists())
        self.assertFalse(Chore.objects.filter(name="Not a demo chore").exists())
        self.assertEqual(Person.objects.count(), 3)

    def test_reset_leaves_the_same_state_as_a_fresh_seed(self):
        self.seed()
        fresh = {p.name: p.effort_total for p in Person.objects.with_effort_totals()}

        self.seed("--reset")

        after_reset = {p.name: p.effort_total for p in Person.objects.with_effort_totals()}
        self.assertEqual(after_reset, fresh)

    def test_reports_what_it_seeded(self):
        output = self.seed()

        self.assertIn("Alex", output)
        self.assertIn("12", output)
        self.assertIn("pending", output)
