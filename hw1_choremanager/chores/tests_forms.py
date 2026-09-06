from django.test import TestCase

from .forms import ChoreForm, PersonForm
from .models import Chore, Person


class PersonFormTests(TestCase):
    def test_valid_name_is_accepted(self):
        form = PersonForm(data={"name": "Alex"})

        self.assertTrue(form.is_valid())

    def test_name_is_required(self):
        form = PersonForm(data={"name": ""})

        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_surrounding_whitespace_is_stripped(self):
        form = PersonForm(data={"name": "  Alex  "})

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["name"], "Alex")

    def test_whitespace_does_not_sneak_a_duplicate_past_the_unique_check(self):
        # The whole point of stripping in clean_name: without it "Alex " is a
        # distinct row, and the roster quietly grows a second Alex whose effort
        # totals are tracked separately.
        Person.objects.create(name="Alex")

        form = PersonForm(data={"name": "Alex "})

        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_duplicate_name_is_a_form_error_not_an_exception(self):
        Person.objects.create(name="Alex")

        form = PersonForm(data={"name": "Alex"})

        self.assertFalse(form.is_valid())


class ChoreFormTests(TestCase):
    def test_valid_chore_is_accepted(self):
        form = ChoreForm(data={"name": "Dishes", "effort": 3})

        self.assertTrue(form.is_valid())

    def test_effort_above_five_is_a_form_error(self):
        # Must fail in the form, so the user sees a message rather than the
        # database check constraint raising an IntegrityError at save time.
        form = ChoreForm(data={"name": "Repaint the house", "effort": 9})

        self.assertFalse(form.is_valid())
        self.assertIn("effort", form.errors)

    def test_effort_below_one_is_a_form_error(self):
        form = ChoreForm(data={"name": "Blink", "effort": 0})

        self.assertFalse(form.is_valid())
        self.assertIn("effort", form.errors)

    def test_the_whole_effort_range_is_accepted(self):
        for effort in range(1, 6):
            form = ChoreForm(data={"name": f"Chore {effort}", "effort": effort})

            self.assertTrue(form.is_valid(), f"effort {effort} should be valid")

    def test_non_numeric_effort_is_a_form_error(self):
        form = ChoreForm(data={"name": "Dishes", "effort": "heavy"})

        self.assertFalse(form.is_valid())
        self.assertIn("effort", form.errors)

    def test_effort_is_required(self):
        form = ChoreForm(data={"name": "Dishes", "effort": ""})

        self.assertFalse(form.is_valid())

    def test_name_whitespace_is_stripped(self):
        form = ChoreForm(data={"name": "  Dishes  ", "effort": 2})

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["name"], "Dishes")

    def test_a_saved_chore_starts_pending_and_unassigned(self):
        form = ChoreForm(data={"name": "Dishes", "effort": 2})
        self.assertTrue(form.is_valid())

        chore = form.save()

        self.assertEqual(chore.status, Chore.Status.PENDING)
        self.assertIsNone(chore.assigned_to)
