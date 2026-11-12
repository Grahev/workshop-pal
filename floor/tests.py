from django.contrib.auth import get_user_model
from django.test import TestCase

from jobs.models import Customer, Job, JobItem, ProductType
from resources.models import Site, WorkCentre
from routing.models import HoldReason, Operation, RouteTemplate, RouteTemplateStep
from routing.services import apply_template

from . import services
from .models import Event
from .services import InvalidTransition

Status = Operation.Status


class FloorTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("op1", password="x")
        cls.site = Site.objects.create(name="Main workshop")
        cls.cut = WorkCentre.objects.create(site=cls.site, name="Cutting", code="CUT")
        cls.weld = WorkCentre.objects.create(site=cls.site, name="Welding", code="WLD")
        cls.product_type = ProductType.objects.create(name="Frame")
        customer = Customer.objects.create(name="Acme")
        job = Job.objects.create(reference="J-001", customer=customer, site=cls.site, title="Bespoke frame")
        cls.item = JobItem.objects.create(job=job, product_type=cls.product_type, name="Frame A")
        template = RouteTemplate.objects.create(name="Frame route", product_type=cls.product_type)
        RouteTemplateStep.objects.create(template=template, sequence=10, name="Cut", work_centre=cls.cut)
        RouteTemplateStep.objects.create(template=template, sequence=20, name="Weld", work_centre=cls.weld)
        apply_template(cls.item, template)
        cls.waiting = HoldReason.objects.create(name="Waiting on material")

    def setUp(self):
        self.op_cut, self.op_weld = self.item.operations.order_by("sequence")


class ApplyTemplateTests(FloorTestCase):
    def test_template_steps_become_pending_operations(self):
        self.assertEqual([o.name for o in (self.op_cut, self.op_weld)], ["Cut", "Weld"])
        self.assertEqual(self.op_cut.status, Status.PENDING)
        self.assertEqual(self.op_weld.work_centre, self.weld)


class TransitionTests(FloorTestCase):
    def test_start_pause_resume_complete(self):
        services.start_operation(self.op_cut, user=self.user, work_centre=self.cut)
        services.pause_operation(self.op_cut, user=self.user)
        services.resume_operation(self.op_cut, user=self.user)
        services.complete_operation(self.op_cut, user=self.user)

        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.COMPLETED)
        self.assertIsNotNone(self.op_cut.started_at)
        self.assertIsNotNone(self.op_cut.completed_at)
        self.assertEqual(
            list(self.op_cut.events.values_list("type", flat=True)),
            ["start", "pause", "resume", "complete"],
        )

    def test_invalid_transition_writes_no_event(self):
        with self.assertRaises(InvalidTransition):
            services.complete_operation(self.op_cut, user=self.user)
        self.assertEqual(Event.objects.count(), 0)
        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.PENDING)

    def test_cannot_start_until_dependencies_done(self):
        with self.assertRaises(InvalidTransition):
            services.start_operation(self.op_weld, user=self.user)

        services.start_operation(self.op_cut, user=self.user)
        services.complete_operation(self.op_cut, user=self.user)
        services.start_operation(self.op_weld, user=self.user)
        self.op_weld.refresh_from_db()
        self.assertEqual(self.op_weld.status, Status.IN_PROGRESS)

    def test_skipped_dependency_does_not_block(self):
        services.skip_operation(self.op_cut, user=self.user)
        services.start_operation(self.op_weld, user=self.user)
        self.op_weld.refresh_from_db()
        self.assertEqual(self.op_weld.status, Status.IN_PROGRESS)

    def test_hold_needs_reason(self):
        with self.assertRaises(InvalidTransition):
            services.record_event(self.op_cut, Event.Type.HOLD, user=self.user)

    def test_hold_and_release_from_pending_returns_to_pending(self):
        services.hold_operation(self.op_cut, self.waiting, user=self.user)
        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.ON_HOLD)
        self.assertEqual(self.op_cut.hold_reason, self.waiting)

        services.release_operation(self.op_cut, user=self.user)
        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.PENDING)
        self.assertIsNone(self.op_cut.hold_reason)

    def test_release_of_running_work_returns_paused(self):
        services.start_operation(self.op_cut, user=self.user)
        services.hold_operation(self.op_cut, self.waiting, user=self.user)
        services.release_operation(self.op_cut, user=self.user)
        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.PAUSED)

    def test_rework_reopens_completed_operation(self):
        services.start_operation(self.op_cut, user=self.user)
        services.complete_operation(self.op_cut, user=self.user)
        services.rework_operation(self.op_cut, user=self.user, note="Wrong length")
        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.PENDING)
        self.assertEqual(self.op_cut.rework_count, 1)
        self.assertIsNone(self.op_cut.completed_at)

    def test_note_does_not_change_status(self):
        services.add_note(self.op_cut, "Check drawing rev", user=self.user)
        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.PENDING)
        self.assertEqual(self.op_cut.events.count(), 1)


class EventImmutabilityTests(FloorTestCase):
    def test_events_cannot_be_edited_or_deleted(self):
        event = services.start_operation(self.op_cut, user=self.user)
        event.note = "changed"
        with self.assertRaises(TypeError):
            event.save()
        with self.assertRaises(TypeError):
            event.delete()


class WorkstationViewTests(FloorTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(f"/floor/station/{self.cut.pk}/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_home_lists_work_centres(self):
        response = self.client.get("/floor/")
        self.assertContains(response, "Cutting")
        self.assertContains(response, "Welding")

    def test_queue_shows_ready_and_upcoming(self):
        response = self.client.get(f"/floor/station/{self.cut.pk}/")
        self.assertEqual([op.name for op in response.context["queue"]["ready"]], ["Cut"])

        response = self.client.get(f"/floor/station/{self.weld.pk}/")
        upcoming = response.context["queue"]["upcoming"]
        self.assertEqual([op.name for op in upcoming], ["Weld"])
        self.assertEqual(upcoming[0].waiting_on, ["Cut"])
        self.assertContains(response, "Cannot start until done: Cut")

    def test_htmx_start_returns_queue_fragment_and_records_event(self):
        response = self.client.post(
            f"/floor/operation/{self.op_cut.pk}/start/", {"station": self.cut.pk}, headers={"HX-Request": "true"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<html")
        self.assertContains(response, "Complete")
        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.IN_PROGRESS)
        event = self.op_cut.events.get()
        self.assertEqual((event.user, event.work_centre), (self.user, self.cut))

    def test_invalid_action_shows_error_in_fragment(self):
        response = self.client.post(
            f"/floor/operation/{self.op_cut.pk}/complete/", {"station": self.cut.pk}, headers={"HX-Request": "true"}
        )
        self.assertContains(response, "Cannot complete")
        self.assertEqual(Event.objects.count(), 0)

    def test_hold_with_reason_from_queue(self):
        self.client.post(
            f"/floor/operation/{self.op_cut.pk}/hold/",
            {"station": self.cut.pk, "hold_reason": self.waiting.pk, "note": "Plate late"},
            headers={"HX-Request": "true"},
        )
        self.op_cut.refresh_from_db()
        self.assertEqual(self.op_cut.status, Status.ON_HOLD)
        self.assertEqual(self.op_cut.events.get().note, "Plate late")

    def test_hard_dependency_disables_start(self):
        response = self.client.get(f"/floor/station/{self.weld.pk}/")
        self.assertContains(response, "Cannot start until done: Cut")

    def test_scan_operation_code_opens_detail(self):
        response = self.client.get(f"/floor/station/{self.cut.pk}/", {"q": f"OP-{self.op_cut.pk}"})
        self.assertRedirects(response, f"/floor/operation/{self.op_cut.pk}/?station={self.cut.pk}")

    def test_search_filters_by_job_reference(self):
        response = self.client.get(f"/floor/station/{self.cut.pk}/", {"q": "nope"})
        self.assertEqual(response.context["queue"]["ready"], [])
        self.assertContains(response, "Nothing matches")

    def test_detail_page_and_plain_form_action(self):
        url = f"/floor/operation/{self.op_cut.pk}/?station={self.cut.pk}"
        self.assertContains(self.client.get(url), "Specification")
        response = self.client.post(
            f"/floor/operation/{self.op_cut.pk}/start/", {"station": self.cut.pk, "next": url}
        )
        self.assertRedirects(response, url)
        self.assertContains(self.client.get(url), "Started")

    def test_unsafe_next_is_ignored(self):
        response = self.client.post(
            f"/floor/operation/{self.op_cut.pk}/start/", {"station": self.cut.pk, "next": "https://evil.example/"}
        )
        self.assertRedirects(response, f"/floor/station/{self.cut.pk}/")
