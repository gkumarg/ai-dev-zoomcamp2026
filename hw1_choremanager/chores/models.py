from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction


class PersonQuerySet(models.QuerySet):
    def with_effort_totals(self):
        """Annotate each person with `effort_total`, heaviest first.

        The annotation is deliberately named differently from the
        `total_effort()` method below: an annotation of the same name would
        shadow the method on the returned instances and break callers that
        use the parentheses form.
        """
        return self.annotate(
            effort_total=models.Sum("history__effort", default=0),
        ).order_by("-effort_total", "name")


class Person(models.Model):
    """Someone who shares the household chores."""

    name = models.CharField(max_length=100, unique=True)

    objects = PersonQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "people"

    def __str__(self):
        return self.name

    def total_effort(self):
        """Cumulative effort this person has completed.

        Use `Person.objects.with_effort_totals()` when you need this for a
        list of people — calling this in a loop is one query per person.
        """
        return self.history.aggregate(total=models.Sum("effort", default=0))["total"]


class Chore(models.Model):
    """A single chore, weighted by how much effort it takes."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ASSIGNED = "assigned", "Assigned"
        DONE = "done", "Done"

    name = models.CharField(max_length=200)
    effort = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Relative effort, from 1 (quick) to 5 (heavy).",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    # Nullable because a pending chore has no owner yet, and because removing
    # a person should leave their chores behind rather than delete them.
    assigned_to = models.ForeignKey(
        Person,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chores",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effort__gte=1, effort__lte=5),
                name="chore_effort_between_1_and_5",
            ),
        ]

    def __str__(self):
        return f"{self.name} (effort {self.effort})"

    @transaction.atomic
    def mark_complete(self, person=None):
        """Mark this chore done and log the effort against `person`.

        Falls back to whoever the chore is assigned to. Returns the new
        `History` entry.
        """
        person = person or self.assigned_to
        if person is None:
            raise ValidationError(
                f"'{self.name}' is not assigned to anyone, so there is nobody "
                f"to credit the effort to."
            )
        if self.status == self.Status.DONE:
            # Completing twice would double-count the effort and quietly skew
            # every fairness calculation downstream.
            raise ValidationError(f"'{self.name}' is already done.")

        entry = History.objects.create(person=person, chore=self, effort=self.effort)
        self.status = self.Status.DONE
        self.assigned_to = person
        self.save(update_fields=["status", "assigned_to"])
        return entry


class History(models.Model):
    """A completed chore, recorded so effort can be balanced over time."""

    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="history",
    )
    # Kept nullable so deleting a chore does not erase the effort someone
    # already put in — that is what the effort snapshot below is for.
    chore = models.ForeignKey(
        Chore,
        null=True,
        on_delete=models.SET_NULL,
        related_name="history",
    )
    effort = models.PositiveSmallIntegerField(
        help_text="The chore's effort at the time it was completed.",
    )
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-completed_at"]
        verbose_name_plural = "history"

    def __str__(self):
        chore = self.chore.name if self.chore else "deleted chore"
        return f"{self.person} completed {chore}"
