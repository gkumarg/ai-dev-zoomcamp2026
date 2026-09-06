from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Chore, History, Person


class TotalEffortTests(TestCase):
    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.sam = Person.objects.create(name="Sam")

    def test_person_with_no_history_has_zero_effort(self):
        self.assertEqual(self.alex.total_effort(), 0)

    def test_total_effort_sums_completed_history(self):
        for effort in (1, 3, 5):
            chore = Chore.objects.create(
                name=f"Chore {effort}", effort=effort, assigned_to=self.alex
            )
            chore.mark_complete()

        self.assertEqual(self.alex.total_effort(), 9)

    def test_total_effort_ignores_other_peoples_history(self):
        Chore.objects.create(name="Dishes", effort=4, assigned_to=self.sam).mark_complete()

        self.assertEqual(self.alex.total_effort(), 0)
        self.assertEqual(self.sam.total_effort(), 4)

    def test_pending_chores_do_not_count_toward_effort(self):
        Chore.objects.create(name="Vacuum", effort=3, assigned_to=self.alex)

        self.assertEqual(self.alex.total_effort(), 0)

    def test_annotation_matches_the_method_and_includes_everyone(self):
        Chore.objects.create(name="Dishes", effort=2, assigned_to=self.alex).mark_complete()

        totals = {
            person.name: person.effort_total
            for person in Person.objects.with_effort_totals()
        }

        self.assertEqual(totals, {"Alex": 2, "Sam": 0})
        self.assertEqual(totals["Alex"], self.alex.total_effort())

    def test_annotation_orders_by_effort_descending(self):
        Chore.objects.create(name="Bins", effort=5, assigned_to=self.sam).mark_complete()

        ordered = [p.name for p in Person.objects.with_effort_totals()]

        self.assertEqual(ordered, ["Sam", "Alex"])


class MarkCompleteTests(TestCase):
    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.chore = Chore.objects.create(name="Dishes", effort=3, assigned_to=self.alex)

    def test_writes_exactly_one_history_entry(self):
        self.chore.mark_complete()

        self.assertEqual(History.objects.count(), 1)

    def test_history_entry_records_person_chore_and_effort(self):
        entry = self.chore.mark_complete()

        self.assertEqual(entry.person, self.alex)
        self.assertEqual(entry.chore, self.chore)
        self.assertEqual(entry.effort, 3)
        self.assertIsNotNone(entry.completed_at)

    def test_status_is_persisted_as_done(self):
        self.chore.mark_complete()
        self.chore.refresh_from_db()

        self.assertEqual(self.chore.status, Chore.Status.DONE)

    def test_effort_snapshot_survives_a_later_chore_change(self):
        self.chore.mark_complete()

        self.chore.effort = 1
        self.chore.save()

        self.assertEqual(History.objects.get().effort, 3)
        self.assertEqual(self.alex.total_effort(), 3)

    def test_explicit_person_overrides_the_assignee(self):
        sam = Person.objects.create(name="Sam")

        entry = self.chore.mark_complete(person=sam)
        self.chore.refresh_from_db()

        self.assertEqual(entry.person, sam)
        self.assertEqual(self.chore.assigned_to, sam)
        self.assertEqual(self.alex.total_effort(), 0)

    def test_unassigned_chore_can_be_completed_by_naming_a_person(self):
        unassigned = Chore.objects.create(name="Bins", effort=2)

        unassigned.mark_complete(person=self.alex)
        unassigned.refresh_from_db()

        self.assertEqual(unassigned.status, Chore.Status.DONE)
        self.assertEqual(unassigned.assigned_to, self.alex)

    def test_unassigned_chore_with_no_person_is_rejected(self):
        unassigned = Chore.objects.create(name="Bins", effort=2)

        with self.assertRaises(ValidationError):
            unassigned.mark_complete()

        self.assertEqual(History.objects.count(), 0)
        self.assertEqual(Chore.objects.get(pk=unassigned.pk).status, Chore.Status.PENDING)

    def test_completing_twice_is_rejected_and_does_not_double_count(self):
        self.chore.mark_complete()

        with self.assertRaises(ValidationError):
            self.chore.mark_complete()

        self.assertEqual(History.objects.count(), 1)
        self.assertEqual(self.alex.total_effort(), 3)
