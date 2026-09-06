import json

from django.test import TestCase

from .agent.tools import ToolError, assign_chore, get_history, get_pending_chores, get_people
from .models import Chore, Person


class GetPeopleTests(TestCase):
    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.sam = Person.objects.create(name="Sam")
        Chore.objects.create(name="Dishes", effort=2, assigned_to=self.alex).mark_complete()

    def test_returns_id_name_and_effort_total(self):
        result = get_people()

        self.assertEqual(
            result,
            [
                {"id": self.alex.id, "name": "Alex", "effort_total": 2},
                {"id": self.sam.id, "name": "Sam", "effort_total": 0},
            ],
        )

    def test_zero_history_is_zero_not_null(self):
        result = get_people()
        sam_entry = next(p for p in result if p["name"] == "Sam")

        self.assertEqual(sam_entry["effort_total"], 0)
        self.assertIsNotNone(sam_entry["effort_total"])

    def test_json_round_trips(self):
        json.dumps(get_people())

    def test_does_not_query_once_per_person(self):
        for i in range(10):
            Person.objects.create(name=f"Extra {i}")

        # One query for the annotated people, full stop — not one more per
        # person for their effort total.
        with self.assertNumQueries(1):
            list(get_people())


class GetPendingChoresTests(TestCase):
    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.pending = Chore.objects.create(name="Vacuum", effort=3)
        self.assigned = Chore.objects.create(
            name="Dishes", effort=2, assigned_to=self.alex, status=Chore.Status.ASSIGNED
        )
        self.done = Chore.objects.create(name="Bins", effort=1, assigned_to=self.alex)
        self.done.mark_complete()

    def test_only_pending_chores_are_returned(self):
        result = get_pending_chores()

        self.assertEqual(result, [{"id": self.pending.id, "name": "Vacuum", "effort": 3}])

    def test_excludes_assigned_and_done(self):
        result = get_pending_chores()
        ids = {chore["id"] for chore in result}

        self.assertNotIn(self.assigned.id, ids)
        self.assertNotIn(self.done.id, ids)

    def test_json_round_trips(self):
        json.dumps(get_pending_chores())


class GetHistoryTests(TestCase):
    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.chores = [
            Chore.objects.create(name=f"Chore {i}", effort=1, assigned_to=self.alex)
            for i in range(5)
        ]
        for chore in self.chores:
            chore.mark_complete()

    def test_shape_and_cumulative_effort(self):
        result = get_history(self.alex.id, limit=10)

        self.assertEqual(result["person_id"], self.alex.id)
        self.assertEqual(result["name"], "Alex")
        self.assertEqual(result["effort_total"], 5)
        self.assertEqual(result["history_count"], 5)
        self.assertEqual(len(result["recent"]), 5)

        entry = result["recent"][0]
        self.assertEqual(set(entry), {"chore_id", "chore_name", "effort", "completed_at"})
        # ISO 8601 string, not a datetime object.
        self.assertIsInstance(entry["completed_at"], str)

    def test_limit_bounds_recent_but_not_the_cumulative_total(self):
        result = get_history(self.alex.id, limit=2)

        self.assertEqual(len(result["recent"]), 2)
        self.assertEqual(result["history_count"], 5)
        self.assertEqual(result["effort_total"], 5)

    def test_unknown_person_raises_tool_error(self):
        with self.assertRaises(ToolError):
            get_history(999999)

    def test_json_round_trips(self):
        json.dumps(get_history(self.alex.id))


class AssignChoreTests(TestCase):
    def setUp(self):
        self.alex = Person.objects.create(name="Alex")
        self.sam = Person.objects.create(name="Sam")
        self.pending = Chore.objects.create(name="Vacuum", effort=3)

    def test_happy_path_sets_status_and_assignee(self):
        result = assign_chore(self.pending.id, self.alex.id)

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Chore.Status.ASSIGNED)
        self.assertEqual(self.pending.assigned_to, self.alex)
        self.assertEqual(
            result,
            {
                "chore_id": self.pending.id,
                "person_id": self.alex.id,
                "chore_name": "Vacuum",
                "person_name": "Alex",
                "status": "assigned",
            },
        )

    def test_json_round_trips(self):
        json.dumps(assign_chore(self.pending.id, self.alex.id))

    def test_unknown_chore_id_is_refused(self):
        with self.assertRaises(ToolError):
            assign_chore(999999, self.alex.id)

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Chore.Status.PENDING)
        self.assertIsNone(self.pending.assigned_to)

    def test_unknown_person_id_is_refused(self):
        with self.assertRaises(ToolError):
            assign_chore(self.pending.id, 999999)

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Chore.Status.PENDING)
        self.assertIsNone(self.pending.assigned_to)

    def test_already_assigned_chore_is_refused(self):
        assign_chore(self.pending.id, self.alex.id)

        with self.assertRaises(ToolError):
            assign_chore(self.pending.id, self.sam.id)

        self.pending.refresh_from_db()
        # Still assigned to Alex — the second call must not have touched it.
        self.assertEqual(self.pending.assigned_to, self.alex)

    def test_done_chore_is_refused(self):
        self.pending.assigned_to = self.alex
        self.pending.save(update_fields=["assigned_to"])
        self.pending.mark_complete()

        with self.assertRaises(ToolError):
            assign_chore(self.pending.id, self.sam.id)

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Chore.Status.DONE)
        self.assertEqual(self.pending.assigned_to, self.alex)

    def test_numeric_string_ids_are_accepted(self):
        # Local tool-calling models routinely emit ids as strings; refusing
        # those would fail the assignment over a JSON type, not a real problem.
        assign_chore(str(self.pending.id), str(self.alex.id))

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Chore.Status.ASSIGNED)
        self.assertEqual(self.pending.assigned_to, self.alex)

    def test_non_numeric_chore_id_is_refused(self):
        with self.assertRaises(ToolError):
            assign_chore("the dishes", self.alex.id)

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Chore.Status.PENDING)

    def test_boolean_id_is_refused_rather_than_coerced(self):
        # bool subclasses int, so True would otherwise become person 1.
        with self.assertRaises(ToolError):
            assign_chore(self.pending.id, True)

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Chore.Status.PENDING)
        self.assertIsNone(self.pending.assigned_to)

    def test_none_person_id_is_refused(self):
        with self.assertRaises(ToolError):
            assign_chore(self.pending.id, None)

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Chore.Status.PENDING)
        self.assertIsNone(self.pending.assigned_to)

    def test_refusal_lists_valid_pending_chore_ids(self):
        other = Chore.objects.create(name="Bins", effort=1)

        with self.assertRaises(ToolError) as ctx:
            assign_chore(999999, self.alex.id)

        message = str(ctx.exception)
        self.assertIn(str(self.pending.id), message)
        self.assertIn(str(other.id), message)
