from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Person(models.Model):
    """Someone who shares the household chores."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "people"

    def __str__(self):
        return self.name


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
