from django.core.management.base import BaseCommand
from django.db import transaction

from chores.models import Chore, History, Person

PEOPLE = ["Alex", "Sam", "Jo"]

# Pending work, spread across the effort range so the agent has something
# interesting to balance rather than four interchangeable chores.
PENDING_CHORES = [
    ("Wash the dishes", 2),
    ("Vacuum the living room", 3),
    ("Take out the bins", 1),
    ("Clean the bathroom", 5),
    ("Mow the lawn", 4),
]

# Deliberately lopsided history: Alex has done far more than Sam. A fair
# assignment has to notice that and load Sam up, which is the whole point of
# reasoning over effort instead of rotating.
COMPLETED = [
    ("Alex", "Deep clean the oven", 5),
    ("Alex", "Scrub the shower", 4),
    ("Alex", "Weed the garden", 3),
    ("Jo", "Fold the laundry", 2),
    ("Jo", "Wipe the counters", 1),
    ("Jo", "Clean the windows", 4),
    ("Sam", "Water the plants", 1),
    ("Sam", "Sweep the porch", 2),
]


class Command(BaseCommand):
    help = "Populate the database with demo people, chores and history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing people, chores and history first.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            History.objects.all().delete()
            Chore.objects.all().delete()
            Person.objects.all().delete()
            self.stdout.write("Cleared existing data.")

        people = {name: Person.objects.get_or_create(name=name)[0] for name in PEOPLE}

        for name, effort in PENDING_CHORES:
            Chore.objects.get_or_create(name=name, defaults={"effort": effort})

        for person_name, chore_name, effort in COMPLETED:
            chore, created = Chore.objects.get_or_create(
                name=chore_name,
                defaults={"effort": effort, "assigned_to": people[person_name]},
            )
            # Go through mark_complete so the demo data is built the same way
            # the app builds real data — status and History stay consistent.
            if created:
                chore.mark_complete(person=people[person_name])

        self.stdout.write(self.style.SUCCESS("Seeded demo data:"))
        for person in Person.objects.with_effort_totals():
            self.stdout.write(f"  {person.name}: {person.effort_total} effort completed")
        pending = Chore.objects.filter(status=Chore.Status.PENDING).count()
        self.stdout.write(f"  {pending} chores pending assignment")
