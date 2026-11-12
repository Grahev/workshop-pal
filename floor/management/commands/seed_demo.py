from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from floor import services
from jobs.models import Customer, Job, JobItem, ProductType
from resources.models import Site, WorkCentre
from routing import services as routing
from routing.models import GateType, HoldReason, LinkKind, RouteTemplate, RouteTemplateStep
from routing.services import apply_template


class Command(BaseCommand):
    help = "Create a small set of demo data for trying the workstation screen. Safe to run once on an empty database."

    @transaction.atomic
    def handle(self, *args, **options):
        if Site.objects.exists():
            self.stdout.write(self.style.WARNING("Data already exists; nothing created."))
            return

        site = Site.objects.create(name="Main workshop")
        centres = {
            code: WorkCentre.objects.create(site=site, name=name, code=code, display_order=order)
            for order, (code, name) in enumerate(
                [("CUT", "Cutting"), ("WLD", "Welding"), ("FIN", "Finishing"), ("ASM", "Assembly"), ("QC", "Inspection")]
            )
        }
        reasons = {
            name: HoldReason.objects.create(name=name)
            for name in ["Waiting on material", "Waiting on drawing", "Waiting on customer approval", "Machine down"]
        }

        frame = ProductType.objects.create(
            name="Steel frame",
            spec_fields=[
                {"key": "width_mm", "label": "Width (mm)", "type": "number"},
                {"key": "height_mm", "label": "Height (mm)", "type": "number"},
                {"key": "material", "label": "Material", "type": "text"},
                {"key": "finish", "label": "Finish", "type": "text"},
            ],
        )
        template = RouteTemplate.objects.create(name="Standard frame", product_type=frame)
        for sequence, (name, code, minutes) in enumerate(
            [("Cut", "CUT", 45), ("Weld", "WLD", 120), ("Powder coat", "FIN", 90), ("Assemble", "ASM", 60), ("Inspect", "QC", 20)],
            start=1,
        ):
            RouteTemplateStep.objects.create(
                template=template, sequence=sequence * 10, name=name,
                work_centre=centres[code], estimated_minutes=minutes,
            )

        today = timezone.localdate()
        jobs = [
            ("J-1001", "Acme Engineering", "Mezzanine support frame", 7, Job.Priority.NORMAL,
             {"width_mm": 2400, "height_mm": 3100, "material": "S275 box 80x80", "finish": "RAL 7016 matt"}),
            ("J-1002", "Northside Fabrication", "Machine guard frame", -1, Job.Priority.URGENT,
             {"width_mm": 1200, "height_mm": 1800, "material": "S275 angle 50x50", "finish": "RAL 1023 gloss"}),
            ("J-1003", "Harbour Fitout", "Bespoke stair stringers", 14, Job.Priority.HIGH,
             {"width_mm": 300, "height_mm": 4200, "material": "S355 plate 12mm", "finish": "Galvanised"}),
        ]
        items = []
        for reference, customer_name, title, due_in, priority, spec in jobs:
            customer, _ = Customer.objects.get_or_create(name=customer_name)
            job = Job.objects.create(
                reference=reference, customer=customer, site=site, title=title,
                due_date=today + timedelta(days=due_in), priority=priority,
            )
            item = JobItem.objects.create(job=job, product_type=frame, name=title, spec=spec)
            apply_template(item, template)
            items.append(item)

        # Put the jobs at different points so every state shows on screen.
        first_cut = items[0].operations.get(name="Cut")
        services.start_operation(first_cut)

        cut, weld = items[1].operations.filter(name__in=["Cut", "Weld"]).order_by("sequence")
        services.start_operation(cut)
        services.complete_operation(cut)
        services.start_operation(weld)

        services.hold_operation(items[2].operations.get(name="Cut"), reasons["Waiting on material"], note="Plate due Thursday")

        # A branching route with a gate: program, cut, fold, then glue and build in
        # parallel; build can start on the other parts but cannot finish before glue,
        # and cannot start at all until the hardware is in.
        hardware = GateType.objects.create(name="Hardware")
        for name in ["Material", "Drawing approval"]:
            GateType.objects.create(name=name)
        panel = ProductType.objects.create(
            name="Folded panel",
            spec_fields=[
                {"key": "width_mm", "label": "Width (mm)", "type": "number"},
                {"key": "height_mm", "label": "Height (mm)", "type": "number"},
                {"key": "finish", "label": "Finish", "type": "choice", "choices": ["Raw", "Galvanised", "Powder coat"]},
            ],
        )
        customer, _ = Customer.objects.get_or_create(name="Riverside Homes")
        job = Job.objects.create(
            reference="J-1004", customer=customer, site=site, title="Cladding panels",
            due_date=today + timedelta(days=10), priority=Job.Priority.NORMAL,
        )
        item = JobItem.objects.create(
            job=job, product_type=panel, name="Cladding panel set", quantity=12,
            spec={"width_mm": 1200, "height_mm": 2400, "finish": "Powder coat"},
        )
        routing.add_steps(item, [
            {"name": "Program", "work_centre": centres["CUT"], "estimated_minutes": 30, "after": []},
            {"name": "Cut", "work_centre": centres["CUT"], "estimated_minutes": 60},
            {"name": "Fold", "work_centre": centres["WLD"], "estimated_minutes": 45},
            {"name": "Glue", "work_centre": centres["FIN"], "estimated_minutes": 40, "after": [("Fold", None, None)]},
            {"name": "Build", "work_centre": centres["ASM"], "estimated_minutes": 90, "gate_types": [hardware],
             "after": [("Fold", None, None), ("Glue", LinkKind.FINISH_TO_FINISH, None)]},
            {"name": "Inspect", "work_centre": centres["QC"], "estimated_minutes": 20},
        ])
        routing.save_as_template(item, "Folded panel (parallel)", product_type=panel)
        for name in ("Program", "Cut"):
            operation = item.operations.get(name=name)
            services.start_operation(operation)
            services.complete_operation(operation)

        self.stdout.write(self.style.SUCCESS("Demo data created: 5 work centres, 4 jobs, 1 branching route with a gate."))
