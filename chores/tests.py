from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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


class EffortConstraintTests(TestCase):
    """Effort is guarded twice — by field validators for forms, and by a
    database check constraint so nothing bypassing the form (the agent's
    `assign_chore`, a shell session, a fixture) can write an unusable value."""

    def test_validators_reject_effort_above_five(self):
        with self.assertRaises(ValidationError):
            Chore(name="Repaint the house", effort=9).full_clean()

    def test_validators_reject_effort_below_one(self):
        with self.assertRaises(ValidationError):
            Chore(name="Blink", effort=0).full_clean()

    def test_validators_accept_the_whole_range(self):
        for effort in range(1, 6):
            Chore(name=f"Chore {effort}", effort=effort).full_clean()

    def test_database_rejects_effort_above_five_even_without_validation(self):
        # .create() skips full_clean(), so only the check constraint stands
        # between a careless caller and a corrupt effort weight.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Chore.objects.create(name="Repaint the house", effort=9)

    def test_database_rejects_effort_below_one(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Chore.objects.create(name="Blink", effort=0)


class PersonUniquenessTests(TestCase):
    def test_duplicate_name_is_rejected(self):
        Person.objects.create(name="Alex")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Person.objects.create(name="Alex")

    def test_names_differing_only_in_case_are_allowed(self):
        # Documents current behaviour rather than endorsing it: the unique
        # constraint is case-sensitive, so "alex" and "Alex" can coexist.
        Person.objects.create(name="Alex")
        Person.objects.create(name="alex")

        self.assertEqual(Person.objects.count(), 2)


class DeletionSemanticsTests(TestCase):
    """The on_delete choices decide what happens to the fairness record, so
    they are behaviour worth pinning down, not incidental configuration."""

    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.chore = Chore.objects.create(name="Dishes", effort=3, assigned_to=self.alex)
        self.chore.mark_complete()

    def test_deleting_a_chore_preserves_the_completed_effort(self):
        # This is why History carries its own effort snapshot: a cascade here
        # would silently rewrite what someone has already done.
        self.chore.delete()

        entry = History.objects.get()
        self.assertIsNone(entry.chore)
        self.assertEqual(entry.effort, 3)
        self.assertEqual(self.alex.total_effort(), 3)

    def test_deleting_a_person_removes_their_history(self):
        # A person who leaves the household leaves the fairness picture too.
        self.alex.delete()

        self.assertEqual(History.objects.count(), 0)

    def test_deleting_a_person_keeps_their_chores_but_unassigns_them(self):
        in_flight = Chore.objects.create(
            name="Vacuum", effort=2, assigned_to=self.alex, status=Chore.Status.ASSIGNED
        )

        self.alex.delete()
        in_flight.refresh_from_db()

        self.assertIsNone(in_flight.assigned_to)
        # The model layer only nulls the assignee; returning the chore to the
        # pending pool is the remove-person view's job (see tests_views).
        self.assertEqual(in_flight.status, Chore.Status.ASSIGNED)


class StringRepresentationTests(TestCase):
    """`__str__` is what the admin and the shell show, so a broken one is a
    real (if small) defect."""

    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.chore = Chore.objects.create(name="Dishes", effort=3, assigned_to=self.alex)

    def test_person(self):
        self.assertEqual(str(self.alex), "Alex")

    def test_chore_includes_effort(self):
        self.assertEqual(str(self.chore), "Dishes (effort 3)")

    def test_history(self):
        entry = self.chore.mark_complete()

        self.assertEqual(str(entry), "Alex completed Dishes")

    def test_history_for_a_deleted_chore_does_not_crash(self):
        entry = self.chore.mark_complete()
        self.chore.delete()
        entry.refresh_from_db()

        self.assertEqual(str(entry), "Alex completed deleted chore")
