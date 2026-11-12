"""Carry existing routes over to links.

- Hard dependencies set by hand become strict links.
- Every other existing route becomes a simple sequence with advisory links,
  which is how it behaved before: earlier steps were shown, never enforced.
- Existing items and templates default to advisory so nothing that could be
  started yesterday is blocked today. New items and templates default to strict.
"""

from django.db import migrations

DONE = ("completed", "skipped")


def convert(apps, schema_editor):
    Operation = apps.get_model("routing", "Operation")
    Dependency = apps.get_model("routing", "Dependency")
    RouteTemplate = apps.get_model("routing", "RouteTemplate")
    JobItem = apps.get_model("jobs", "JobItem")

    links = {}
    old_links = Operation.depends_on.through.objects.values_list("from_operation_id", "to_operation_id")
    for operation_id, predecessor_id in old_links:
        links[(operation_id, predecessor_id)] = "strict"

    by_item = {}
    for pk, item_id in Operation.objects.order_by("item_id", "sequence", "id").values_list("pk", "item_id"):
        by_item.setdefault(item_id, []).append(pk)
    for operations in by_item.values():
        for previous, current in zip(operations, operations[1:]):
            links.setdefault((current, previous), "advisory")

    Dependency.objects.bulk_create(
        Dependency(operation_id=operation, predecessor_id=predecessor, kind="finish_to_start", enforcement=enforcement)
        for (operation, predecessor), enforcement in links.items()
    )
    JobItem.objects.update(link_enforcement="advisory")
    RouteTemplate.objects.update(default_enforcement="advisory", simple_sequence=True)


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0002_links_and_gates"),
        ("jobs", "0002_jobitem_link_enforcement"),
    ]

    operations = [
        migrations.RunPython(convert, migrations.RunPython.noop),
        migrations.RemoveField(model_name="operation", name="depends_on"),
    ]
