from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ChoreForm, PersonForm
from .models import Chore, Person

# --- People -----------------------------------------------------------


def person_list(request):
    people = Person.objects.with_effort_totals()
    form = PersonForm()
    return render(request, "chores/person_list.html", {"people": people, "form": form})


@require_POST
def person_add(request):
    form = PersonForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, f"Added {form.cleaned_data['name']} to the roster.")
        return redirect("chores:person_list")

    # Re-render the list with the bound (errored) form instead of redirecting,
    # so validation messages (e.g. the duplicate-name error) survive.
    people = Person.objects.with_effort_totals()
    return render(request, "chores/person_list.html", {"people": people, "form": form})


def person_remove_confirm(request, pk):
    person = get_object_or_404(Person, pk=pk)
    return render(request, "chores/person_confirm_remove.html", {"person": person})


@require_POST
def person_remove(request, pk):
    person = get_object_or_404(Person, pk=pk)
    with transaction.atomic():
        # Deleting a person nulls out assigned_to on their chores (SET_NULL),
        # which would otherwise strand those chores in "assigned" status with
        # no one to do them. Send them back to the pending pool instead.
        person.chores.filter(status=Chore.Status.ASSIGNED).update(
            status=Chore.Status.PENDING, assigned_to=None
        )
        name = person.name
        person.delete()
    messages.success(request, f"Removed {name}. Their assigned chores are pending again.")
    return redirect("chores:person_list")


# --- Chores -------------------------------------------------------------


def chore_list(request):
    status_filter = request.GET.get("status", "")
    chores = Chore.objects.select_related("assigned_to")
    if status_filter in Chore.Status.values:
        chores = chores.filter(status=status_filter)
    form = ChoreForm()
    return render(
        request,
        "chores/chore_list.html",
        {
            "chores": chores,
            "form": form,
            "status_filter": status_filter,
            "statuses": Chore.Status.choices,
        },
    )


@require_POST
def chore_add(request):
    form = ChoreForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, f"Added chore '{form.cleaned_data['name']}'.")
        return redirect("chores:chore_list")

    chores = Chore.objects.select_related("assigned_to")
    return render(
        request,
        "chores/chore_list.html",
        {
            "chores": chores,
            "form": form,
            "status_filter": "",
            "statuses": Chore.Status.choices,
        },
    )


@require_POST
def chore_complete(request, pk):
    chore = get_object_or_404(Chore, pk=pk)
    try:
        chore.mark_complete()
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"Marked '{chore.name}' complete.")
    return redirect("chores:chore_list")


# --- Assign (stub) --------------------------------------------------------


def assign(request):
    return render(request, "chores/assign_stub.html")


# --- Status ---------------------------------------------------------------


def status(request):
    people = Person.objects.with_effort_totals()
    return render(request, "chores/status.html", {"people": people})
