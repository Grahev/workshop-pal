from django.contrib.auth import get_user_model
from django.test import TestCase

from floor import services as floor
from floor.models import Issue
from resources.models import Site, WorkCentre
from routing.models import Operation, RouteTemplate, RouteTemplateStep
from routing.services import RouteError, add_operation, move_operation, remove_operation

from . import services
from .models import Customer, Job, JobItem, ProductType


class JobsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.office = User.objects.create_user("office", password="x", role="office")
        cls.operator = User.objects.create_user("op", password="x", role="operator")
        cls.site = Site.objects.create(name="Main")
        cls.cut = WorkCentre.objects.create(site=cls.site, name="Cutting", code="CUT")
        cls.weld = WorkCentre.objects.create(site=cls.site, name="Welding", code="WLD")
        cls.frame = ProductType.objects.create(
            name="Frame",
            spec_fields=[
                {"key": "width_mm", "label": "Width (mm)", "type": "number", "required": True},
                {"key": "finish", "label": "Finish", "type": "choice", "choices": ["Raw", "Painted"]},
            ],
        )
        template = RouteTemplate.objects.create(name="Frame route", product_type=cls.frame)
        RouteTemplateStep.objects.create(template=template, sequence=10, name="Cut", work_centre=cls.cut)
        RouteTemplateStep.objects.create(template=template, sequence=20, name="Weld", work_centre=cls.weld)
        cls.customer = Customer.objects.create(name="Acme")
        cls.job = Job.objects.create(reference="J-1", customer=cls.customer, site=cls.site, title="Frame job")

    def setUp(self):
        self.client.force_login(self.office)

    def make_item(self):
        item = JobItem.objects.create(job=self.job, product_type=self.frame, name="Frame A")
        add_operation(item, "Cut", self.cut)
        add_operation(item, "Weld", self.weld)
        add_operation(item, "Paint", self.weld)
        return item


class JobViewTests(JobsTestCase):
    def test_list_search_and_archive_filter(self):
        Job.objects.create(reference="J-2", customer=self.customer, site=self.site, title="Old", is_archived=True)
        self.assertContains(self.client.get("/jobs/"), "J-1")
        self.assertNotContains(self.client.get("/jobs/"), "J-2")
        self.assertContains(self.client.get("/jobs/?show=archived"), "J-2")
        self.assertNotContains(self.client.get("/jobs/?q=nothing"), "Frame job")

    def test_create_job_with_new_customer(self):
        response = self.client.post("/jobs/new/", {
            "reference": "J-50", "title": "Gate", "new_customer": "Brand New Ltd",
            "site": self.site.pk, "priority": 20, "due_date": "2026-12-01",
        })
        job = Job.objects.get(reference="J-50")
        self.assertRedirects(response, f"/jobs/{job.pk}/")
        self.assertEqual(job.customer.name, "Brand New Ltd")
        self.assertEqual(job.created_by, self.office)

    def test_job_needs_a_customer(self):
        response = self.client.post("/jobs/new/", {"reference": "J-51", "title": "Gate", "site": self.site.pk, "priority": 20})
        self.assertContains(response, "Choose a customer or add a new one.")
        self.assertFalse(Job.objects.filter(reference="J-51").exists())

    def test_edit_and_archive(self):
        self.client.post(f"/jobs/{self.job.pk}/edit/", {
            "reference": "J-1", "title": "Renamed", "customer": self.customer.pk, "site": self.site.pk, "priority": 30,
        })
        self.job.refresh_from_db()
        self.assertEqual((self.job.title, self.job.priority), ("Renamed", 30))
        self.client.post(f"/jobs/{self.job.pk}/archive/")
        self.job.refresh_from_db()
        self.assertTrue(self.job.is_archived)

    def test_detail_shows_items_and_timeline(self):
        item = self.make_item()
        floor.start_operation(item.operations.get(name="Cut"), user=self.operator)
        response = self.client.get(f"/jobs/{self.job.pk}/")
        self.assertContains(response, "Frame A")
        self.assertContains(response, "Started")

    def test_operator_can_view_but_not_change(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get("/jobs/").status_code, 200)
        self.assertEqual(self.client.get(f"/jobs/{self.job.pk}/").status_code, 200)
        self.assertEqual(self.client.get("/jobs/new/").status_code, 403)
        self.assertEqual(self.client.post(f"/jobs/{self.job.pk}/archive/").status_code, 403)


class ItemTests(JobsTestCase):
    def test_new_item_gets_route_from_product_type_template(self):
        response = self.client.post(f"/jobs/{self.job.pk}/items/new/", {"name": "Frame B", "product_type": self.frame.pk, "quantity": 2})
        item = JobItem.objects.get(name="Frame B")
        self.assertRedirects(response, f"/jobs/items/{item.pk}/")
        self.assertEqual(list(item.operations.values_list("name", flat=True)), ["Cut", "Weld"])

    def test_spec_form_saves_typed_values_and_versions(self):
        item = self.make_item()
        self.client.post(f"/jobs/items/{item.pk}/spec/", {
            "spec__width_mm": "1200", "spec__finish": "Painted", "extra_key": "Glass type", "extra_value": "Toughened",
        })
        item.refresh_from_db()
        self.assertEqual(item.spec, {"width_mm": 1200, "finish": "Painted", "glass_type": "Toughened"})
        self.assertEqual(item.spec_version, 2)
        self.assertEqual(item.spec_revisions.get().spec, {})

        # The extra field is kept on the next save and shown as its own field.
        self.assertIn("glass_type", [f["key"] for f in services.spec_form_fields(item)])
        self.client.post(f"/jobs/items/{item.pk}/spec/", {
            "spec__width_mm": "1250.5", "spec__finish": "Painted", "spec__glass_type": "Toughened",
        })
        item.refresh_from_db()
        self.assertEqual(item.spec["width_mm"], 1250.5)
        self.assertEqual(item.spec_version, 3)

    def test_required_spec_field_enforced(self):
        item = self.make_item()
        response = self.client.post(f"/jobs/items/{item.pk}/spec/", {"spec__finish": "Raw"}, follow=True)
        self.assertContains(response, "Width (mm) is required.")
        item.refresh_from_db()
        self.assertEqual(item.spec_version, 1)

    def test_item_page_renders_for_office_and_operator(self):
        item = self.make_item()
        self.assertContains(self.client.get(f"/jobs/items/{item.pk}/"), "Save spec")
        self.client.force_login(self.operator)
        response = self.client.get(f"/jobs/items/{item.pk}/")
        self.assertNotContains(response, "Save spec")
        self.assertContains(response, "Cut")


class RouteEditingTests(JobsTestCase):
    def names(self, item):
        return list(item.operations.order_by("sequence").values_list("name", flat=True))

    def test_add_and_reorder(self):
        item = self.make_item()
        self.client.post(f"/jobs/items/{item.pk}/route/add/", {"name": "Inspect", "work_centre": self.cut.pk, "estimated_minutes": 15})
        self.assertEqual(self.names(item), ["Cut", "Weld", "Paint", "Inspect"])

        inspect = item.operations.get(name="Inspect")
        self.client.post(f"/jobs/operations/{inspect.pk}/move/up/")
        self.assertEqual(self.names(item), ["Cut", "Weld", "Inspect", "Paint"])
        self.client.post(f"/jobs/operations/{inspect.pk}/move/down/")
        self.assertEqual(self.names(item), ["Cut", "Weld", "Paint", "Inspect"])

        # Moving past either end changes nothing.
        move_operation(item.operations.get(name="Cut"), "up")
        self.assertEqual(self.names(item), ["Cut", "Weld", "Paint", "Inspect"])

    def test_remove_only_untouched_steps(self):
        item = self.make_item()
        cut, weld, _ = item.operations.order_by("sequence")
        floor.start_operation(cut)
        with self.assertRaises(RouteError):
            remove_operation(cut)
        self.client.post(f"/jobs/operations/{weld.pk}/remove/")
        self.assertEqual(self.names(item), ["Cut", "Paint"])

    def test_skip_keeps_step_in_history(self):
        item = self.make_item()
        weld = item.operations.get(name="Weld")
        self.client.post(f"/jobs/operations/{weld.pk}/skip/")
        weld.refresh_from_db()
        self.assertEqual(weld.status, Operation.Status.SKIPPED)
        self.assertEqual(weld.events.get().user, self.office)

    def test_edit_step_only_before_it_starts(self):
        item = self.make_item()
        cut, weld, _ = item.operations.order_by("sequence")
        self.client.post(f"/jobs/operations/{weld.pk}/edit/", {"name": "MIG weld", "work_centre": self.cut.pk, "estimated_minutes": 90})
        weld.refresh_from_db()
        self.assertEqual((weld.name, weld.work_centre, weld.estimated_minutes), ("MIG weld", self.cut, 90))

        floor.start_operation(cut)
        self.client.post(f"/jobs/operations/{cut.pk}/edit/", {"name": "Changed", "work_centre": self.cut.pk, "estimated_minutes": 1})
        cut.refresh_from_db()
        self.assertEqual(cut.name, "Cut")

    def test_apply_template_to_item_without_route(self):
        item = JobItem.objects.create(job=self.job, name="Loose item")
        template = RouteTemplate.objects.get()
        self.client.post(f"/jobs/items/{item.pk}/route/apply/", {"template": template.pk})
        self.assertEqual(self.names(item), ["Cut", "Weld"])

    def test_operator_cannot_edit_route(self):
        item = self.make_item()
        self.client.force_login(self.operator)
        response = self.client.post(f"/jobs/items/{item.pk}/route/add/", {"name": "X", "work_centre": self.cut.pk, "estimated_minutes": 0})
        self.assertEqual(response.status_code, 403)


class IssueTests(JobsTestCase):
    def test_raise_from_floor_and_resolve_from_office(self):
        item = self.make_item()
        cut = item.operations.get(name="Cut")
        self.client.force_login(self.operator)
        self.client.post(f"/floor/operation/{cut.pk}/issue/", {"category": "ncr", "description": "Cut 5mm short", "station": self.cut.pk})
        issue = Issue.objects.get()
        self.assertEqual((issue.category, issue.raised_by, issue.is_open), ("ncr", self.operator, True))
        self.assertIn("Cut 5mm short", cut.events.get().note)

        # Operators cannot resolve; office can.
        self.assertEqual(self.client.post(f"/jobs/issues/{issue.pk}/resolve/").status_code, 403)
        self.client.force_login(self.office)
        self.client.post(f"/jobs/issues/{issue.pk}/resolve/", {"resolution": "Recut"})
        issue.refresh_from_db()
        self.assertFalse(issue.is_open)
        self.assertEqual(issue.resolution, "Recut")
        self.assertContains(self.client.get(f"/jobs/{self.job.pk}/"), "Cut 5mm short")

    def test_rework_is_office_only(self):
        item = self.make_item()
        cut = item.operations.get(name="Cut")
        floor.start_operation(cut)
        floor.complete_operation(cut)

        self.client.force_login(self.operator)
        self.client.post(f"/floor/operation/{cut.pk}/rework/", {"note": "Redo"})
        cut.refresh_from_db()
        self.assertEqual(cut.status, Operation.Status.COMPLETED)

        self.client.force_login(self.office)
        self.client.post(f"/floor/operation/{cut.pk}/rework/", {"note": "Redo"})
        cut.refresh_from_db()
        self.assertEqual((cut.status, cut.rework_count), (Operation.Status.PENDING, 1))
