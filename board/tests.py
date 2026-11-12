from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from floor import services as floor
from jobs.models import Customer, Job, JobItem
from resources.models import Site, WorkCentre
from routing.models import HoldReason
from routing.services import add_steps

from . import selectors


class BoardTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("op", password="x")
        cls.site = Site.objects.create(name="Main")
        cls.cut = WorkCentre.objects.create(site=cls.site, name="Cutting", code="CUT")
        cls.weld = WorkCentre.objects.create(site=cls.site, name="Welding", code="WLD")
        cls.reason = HoldReason.objects.create(name="Waiting on material")
        cls.customer = Customer.objects.create(name="Acme")
        cls.today = timezone.localdate()

    def make_item(self, reference, due_in=None, route=True):
        job = Job.objects.create(
            reference=reference, customer=self.customer, site=self.site, title=f"Job {reference}",
            due_date=self.today + timedelta(days=due_in) if due_in is not None else None,
        )
        item = JobItem.objects.create(job=job, name=f"Item {reference}")
        if route:
            add_steps(item, [{"name": "Cut", "work_centre": self.cut}, {"name": "Weld", "work_centre": self.weld}])
        return item

    def row_for(self, item):
        rows, _ = selectors.board_rows(status="")
        return next((row for row in rows if row.item.pk == item.pk), None)


class RowStateTests(BoardTestCase):
    def test_item_without_route_is_flagged(self):
        item = self.make_item("J-1", route=False)
        self.assertEqual(self.row_for(item).state, selectors.NO_ROUTE)

    def test_current_step_and_progress_follow_events(self):
        item = self.make_item("J-1")
        cut, weld = item.operations.order_by("sequence")
        row = self.row_for(item)
        self.assertEqual((row.state, row.current, row.done, row.total), ("pending", cut, 0, 2))

        floor.start_operation(cut)
        self.assertEqual(self.row_for(item).state, "in_progress")

        floor.complete_operation(cut)
        row = self.row_for(item)
        self.assertEqual((row.state, row.current, row.done, row.percent), ("pending", weld, 1, 50))

    def test_hold_takes_precedence_and_records_since(self):
        item = self.make_item("J-1")
        cut = item.operations.get(name="Cut")
        floor.hold_operation(cut, self.reason)
        row = self.row_for(item)
        self.assertEqual(row.state, "on_hold")
        self.assertIsNotNone(row.held_since)

    def test_completed_items_leave_the_active_board(self):
        item = self.make_item("J-1")
        for op in item.operations.order_by("sequence"):
            floor.start_operation(op)
            floor.complete_operation(op)
        self.assertIsNone(self.row_for(item))
        rows, summary = selectors.board_rows(status="completed")
        self.assertEqual([row.item for row in rows], [item])
        self.assertEqual(summary["active"], 0)

    def test_late_and_at_risk_flags(self):
        late = self.row_for(self.make_item("J-1", due_in=-1))
        soon = self.row_for(self.make_item("J-2", due_in=1))
        fine = self.row_for(self.make_item("J-3", due_in=30))
        self.assertEqual((late.is_late, late.at_risk), (True, False))
        self.assertEqual((soon.is_late, soon.at_risk), (False, True))
        self.assertEqual((fine.is_late, fine.at_risk), (False, False))

    def test_archived_jobs_are_hidden(self):
        item = self.make_item("J-1")
        item.job.is_archived = True
        item.job.save()
        self.assertIsNone(self.row_for(item))


class FilterTests(BoardTestCase):
    def test_summary_and_status_filters(self):
        held = self.make_item("J-1", due_in=-2)
        floor.hold_operation(held.operations.get(name="Cut"), self.reason)
        running = self.make_item("J-2", due_in=30)
        floor.start_operation(running.operations.get(name="Cut"))
        self.make_item("J-3", due_in=30)

        rows, summary = selectors.board_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary, {"active": 3, "in_progress": 1, "on_hold": 1, "late": 1, "at_risk": 0})

        self.assertEqual([r.item for r in selectors.board_rows(status="on_hold")[0]], [held])
        self.assertEqual([r.item for r in selectors.board_rows(status="late")[0]], [held])
        self.assertEqual([r.item for r in selectors.board_rows(status="attention")[0]], [held])
        self.assertEqual([r.item for r in selectors.board_rows(query="J-2")[0]], [running])

    def test_work_centre_filter_uses_current_step(self):
        item = self.make_item("J-1")
        cut = item.operations.get(name="Cut")
        self.assertEqual(len(selectors.board_rows(work_centre_id=self.cut.pk)[0]), 1)
        self.assertEqual(len(selectors.board_rows(work_centre_id=self.weld.pk)[0]), 0)
        floor.start_operation(cut)
        floor.complete_operation(cut)
        self.assertEqual(len(selectors.board_rows(work_centre_id=self.weld.pk)[0]), 1)


class LaneTests(BoardTestCase):
    def test_operations_grouped_by_work_centre(self):
        item = self.make_item("J-1")
        floor.start_operation(item.operations.get(name="Cut"))
        lanes = {lane.work_centre.code: lane for lane in selectors.board_lanes()}
        self.assertEqual([op.name for op in lanes["CUT"].active], ["Cut"])
        self.assertEqual([op.name for op in lanes["WLD"].upcoming], ["Weld"])
        self.assertEqual(lanes["WLD"].upcoming[0].waiting_on, ["Cut"])


class BoardViewTests(BoardTestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get("/board/").status_code, 302)

    def test_both_views_render(self):
        item = self.make_item("J-1", due_in=-1)
        floor.hold_operation(item.operations.get(name="Cut"), self.reason)
        response = self.client.get("/board/")
        self.assertContains(response, "J-1")
        self.assertContains(response, "Late")
        self.assertContains(response, "Waiting on material")
        response = self.client.get("/board/?view=centres")
        self.assertContains(response, "Cutting")
        self.assertContains(response, "Coming up")

    def test_htmx_poll_returns_fragment(self):
        self.make_item("J-1")
        response = self.client.get("/board/?view=centres", headers={"HX-Request": "true"})
        self.assertNotContains(response, "<html")
        self.assertContains(response, "Active items")
        self.assertContains(response, "J-1")

    def test_root_sends_operator_to_workstations_and_office_to_board(self):
        self.assertRedirects(self.client.get("/"), "/floor/")
        self.user.role = "office"
        self.user.save()
        self.assertRedirects(self.client.get("/"), "/board/")
