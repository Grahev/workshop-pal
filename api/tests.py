import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from attachments.models import Attachment
from floor.models import Event, Issue
from jobs.models import Customer, Job, JobItem, ProductType
from resources.models import Site, WorkCentre
from routing.models import GateType, HoldReason, Operation, RouteTemplate, RouteTemplateStep

from .models import ApiKey


class ApiTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.office = User.objects.create_user("erp", password="x", role="office")
        cls.operator = User.objects.create_user("scanner", password="x", role="operator")
        _, cls.office_key = ApiKey.issue("ERP", cls.office)
        _, cls.operator_key = ApiKey.issue("Scanner", cls.operator)

        cls.site = Site.objects.create(name="Main")
        cls.cut = WorkCentre.objects.create(site=cls.site, name="Cutting", code="CUT")
        cls.weld = WorkCentre.objects.create(site=cls.site, name="Welding", code="WLD")
        cls.reason = HoldReason.objects.create(name="Waiting on material")
        cls.frame = ProductType.objects.create(
            name="Frame",
            spec_fields=[
                {"key": "width_mm", "label": "Width (mm)", "type": "number", "required": True},
                {"key": "finish", "label": "Finish", "type": "choice", "choices": ["Raw", "Painted"]},
            ],
        )
        cls.template = RouteTemplate.objects.create(name="Frame route", product_type=cls.frame)
        RouteTemplateStep.objects.create(template=cls.template, sequence=10, name="Cut", work_centre=cls.cut, estimated_minutes=30)
        RouteTemplateStep.objects.create(template=cls.template, sequence=20, name="Weld", work_centre=cls.weld, estimated_minutes=60)

    def call(self, method, path, body=None, key=None, **extra):
        headers = {"Authorization": f"Bearer {key or self.office_key}"}
        kwargs = {"headers": headers, **extra}
        if body is not None:
            kwargs.update(data=json.dumps(body), content_type="application/json")
        return getattr(self.client, method)(f"/api/v1{path}", **kwargs)

    def make_job(self, **overrides):
        body = {
            "reference": "J-1", "title": "Frame job", "customer": "Acme",
            "items": [{"name": "Frame A", "product_type": "Frame", "spec": {"width_mm": 1200}}],
        }
        body.update(overrides)
        response = self.call("post", "/jobs", body)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()


class AuthTests(ApiTestCase):
    def test_key_required(self):
        self.assertEqual(self.client.get("/api/v1/jobs").status_code, 401)
        self.assertEqual(self.call("get", "/jobs", key="wp_wrong").status_code, 401)
        self.assertEqual(self.call("get", "/jobs").status_code, 200)

    def test_inactive_key_and_inactive_user_rejected(self):
        ApiKey.objects.filter(name="ERP").update(is_active=False)
        self.assertEqual(self.call("get", "/jobs").status_code, 401)
        self.operator.is_active = False
        self.operator.save()
        self.assertEqual(self.call("get", "/jobs", key=self.operator_key).status_code, 401)

    def test_only_hash_is_stored(self):
        key = ApiKey.objects.get(name="ERP")
        self.assertNotIn(self.office_key, (key.key_hash, key.prefix))
        self.assertEqual(key.key_hash, ApiKey.hash(self.office_key))

    def test_operator_key_can_read_but_not_change_jobs(self):
        self.assertEqual(self.call("get", "/jobs", key=self.operator_key).status_code, 200)
        response = self.call("post", "/jobs", {"title": "X", "customer": "Acme"}, key=self.operator_key)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Job.objects.exists())

    def test_docs_need_a_login(self):
        self.assertEqual(self.client.get("/api/v1/docs").status_code, 302)
        self.client.force_login(self.office)
        self.assertEqual(self.client.get("/api/v1/docs").status_code, 200)


class ReferenceTests(ApiTestCase):
    def test_reference_lists(self):
        self.assertEqual([w["code"] for w in self.call("get", "/work-centres").json()], ["CUT", "WLD"])
        self.assertEqual(self.call("get", "/product-types").json()[0]["spec_fields"][0]["key"], "width_mm")
        self.assertEqual(len(self.call("get", "/route-templates").json()[0]["steps"]), 2)
        self.assertEqual(self.call("get", "/hold-reasons").json()[0]["name"], "Waiting on material")

    def test_create_customer_is_idempotent(self):
        first = self.call("post", "/customers", {"name": "Acme"})
        second = self.call("post", "/customers", {"name": "Acme"})
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()["id"], second.json()["id"])


class JobTests(ApiTestCase):
    def test_create_job_with_items_and_default_route(self):
        job = self.make_job(due_date="2026-12-01", priority="high", customer_reference="PO-9")
        self.assertEqual((job["reference"], job["priority"], job["customer"]["name"]), ("J-1", "high", "Acme"))
        item = job["items"][0]
        self.assertEqual(item["spec"], {"width_mm": 1200})
        self.assertEqual([(op["name"], op["work_centre"], op["status"]) for op in item["operations"]],
                         [("Cut", "CUT", "pending"), ("Weld", "WLD", "pending")])
        self.assertEqual(item["operations"][0]["code"], f"OP-{item['operations'][0]['id']}")
        self.assertEqual(Job.objects.get().created_by, self.office)

    def test_explicit_route_overrides_template(self):
        job = self.make_job(items=[{
            "name": "Special", "product_type": "Frame", "spec": {"width_mm": 5},
            "operations": [
                {"name": "Laser", "work_centre": "CUT", "estimated_minutes": 10},
                {"name": "Fold", "work_centre_id": self.weld.pk},
            ],
        }])
        self.assertEqual([op["name"] for op in job["items"][0]["operations"]], ["Laser", "Fold"])

    def test_reference_generated_and_duplicates_rejected(self):
        job = self.call("post", "/jobs", {"title": "No ref", "customer": "Acme"}).json()
        self.assertTrue(job["reference"].startswith("J-"))
        duplicate = self.call("post", "/jobs", {"reference": job["reference"], "title": "Again", "customer": "Acme"})
        self.assertEqual(duplicate.status_code, 409)

    def test_invalid_item_saves_nothing(self):
        response = self.call("post", "/jobs", {
            "reference": "J-9", "title": "Bad", "customer": "New Co",
            "items": [
                {"name": "Good", "product_type": "Frame", "spec": {"width_mm": 1}},
                {"name": "Bad", "product_type": "Frame", "spec": {"finish": "Chrome"}},
            ],
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn("Width (mm) is required", response.json()["detail"])
        self.assertIn("Finish must be one of", response.json()["detail"])
        self.assertFalse(Job.objects.exists())
        self.assertFalse(JobItem.objects.exists())
        self.assertFalse(Customer.objects.filter(name="New Co").exists())

    def test_unknown_lookups_give_clear_errors(self):
        response = self.call("post", "/jobs", {"title": "X", "customer": "Acme", "items": [{"name": "A", "product_type": "Nope"}]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Product type not found: Nope")
        self.assertEqual(self.call("post", "/jobs", {"title": "X"}).status_code, 422)

    def test_list_filter_get_patch_archive(self):
        job = self.make_job()
        self.make_job(reference="J-2", title="Other", items=[])
        listing = self.call("get", "/jobs").json()
        self.assertEqual(listing["count"], 2)
        self.assertEqual([j["reference"] for j in self.call("get", "/jobs?reference=J-2").json()["items"]], ["J-2"])
        self.assertEqual(self.call("get", "/jobs?q=other").json()["count"], 1)

        patched = self.call("patch", f"/jobs/{job['id']}", {"title": "Renamed", "priority": "urgent", "due_date": "2026-12-24"}).json()
        self.assertEqual((patched["title"], patched["priority"], patched["due_date"]), ("Renamed", "urgent", "2026-12-24"))
        self.assertIsNone(self.call("patch", f"/jobs/{job['id']}", {"due_date": None}).json()["due_date"])
        self.assertEqual(self.call("patch", f"/jobs/{job['id']}", {"reference": "J-2"}).status_code, 409)

        self.assertTrue(self.call("post", f"/jobs/{job['id']}/archive").json()["is_archived"])
        self.assertEqual(self.call("get", "/jobs").json()["count"], 1)
        self.assertEqual(self.call("get", "/jobs?archived=true").json()["count"], 1)
        self.assertFalse(self.call("post", f"/jobs/{job['id']}/restore").json()["is_archived"])
        self.assertEqual(self.call("get", "/jobs/99999").status_code, 404)


class ItemAndRouteTests(ApiTestCase):
    def test_add_item_update_and_spec_versions(self):
        job = self.make_job(items=[])
        item = self.call("post", f"/jobs/{job['id']}/items", {"name": "Late add", "product_type_id": self.frame.pk, "spec": {"width_mm": 10}})
        self.assertEqual(item.status_code, 201)
        item_id = item.json()["id"]

        self.assertEqual(self.call("patch", f"/items/{item_id}", {"quantity": 4}).json()["quantity"], 4)

        bad = self.call("put", f"/items/{item_id}/spec", {"spec": {"width_mm": "wide"}})
        self.assertEqual(bad.status_code, 422)
        updated = self.call("put", f"/items/{item_id}/spec", {"spec": {"width_mm": 20, "finish": "Painted"}, "note": "Customer change"}).json()
        self.assertEqual((updated["spec_version"], updated["spec"]["width_mm"]), (2, 20))
        history = self.call("get", f"/items/{item_id}/spec-history").json()
        self.assertEqual((history[0]["version"], history[0]["spec"], history[0]["changed_by"]), (1, {"width_mm": 10}, "erp"))

    def test_route_editing(self):
        item = self.make_job()["items"][0]
        cut_id, weld_id = [op["id"] for op in item["operations"]]

        added = self.call("post", f"/items/{item['id']}/operations", {"name": "Inspect", "work_centre": "cut", "estimated_minutes": 5})
        self.assertEqual(added.status_code, 201)
        inspect_id = added.json()["id"]

        order = self.call("post", f"/operations/{inspect_id}/move", {"direction": "up"}).json()
        self.assertEqual([op["name"] for op in order], ["Cut", "Inspect", "Weld"])

        changed = self.call("patch", f"/operations/{weld_id}", {"name": "MIG weld", "estimated_minutes": 0}).json()
        self.assertEqual((changed["name"], changed["estimated_minutes"], changed["work_centre"]), ("MIG weld", 0, "WLD"))

        self.assertEqual(self.call("delete", f"/operations/{inspect_id}").status_code, 204)
        self.call("post", f"/operations/{cut_id}/events", {"type": "start"})
        self.assertEqual(self.call("delete", f"/operations/{cut_id}").status_code, 409)
        self.assertEqual(self.call("patch", f"/operations/{cut_id}", {"name": "X"}).status_code, 409)

    def test_apply_template(self):
        job = self.make_job(items=[{"name": "Bare", "apply_default_route": False}])
        item_id = job["items"][0]["id"]
        applied = self.call("post", f"/items/{item_id}/route/apply-template?template_id={self.template.pk}").json()
        self.assertEqual(len(applied["operations"]), 2)
        self.assertEqual(self.call("post", f"/items/{item_id}/route/apply-template?template_id={self.template.pk}").status_code, 409)


class FloorTests(ApiTestCase):
    def setUp(self):
        self.item = self.make_job()["items"][0]
        self.cut_id, self.weld_id = [op["id"] for op in self.item["operations"]]

    def event(self, operation_id, body, key=None):
        return self.call("post", f"/operations/{operation_id}/events", body, key=key or self.operator_key)

    def test_full_flow_with_operator_key(self):
        started = self.event(self.cut_id, {"type": "start", "work_centre_id": self.cut.pk})
        self.assertEqual(started.status_code, 201)
        self.assertEqual(started.json()["operation"]["status"], "in_progress")
        self.assertEqual(started.json()["event"]["user"], "scanner")

        held = self.event(self.cut_id, {"type": "hold", "hold_reason": "waiting on material", "note": "Plate late"}).json()
        self.assertEqual((held["operation"]["status"], held["operation"]["hold_reason"]), ("on_hold", "Waiting on material"))
        self.assertEqual(self.event(self.cut_id, {"type": "release"}).json()["operation"]["status"], "paused")
        self.event(self.cut_id, {"type": "resume"})
        self.assertEqual(self.event(self.cut_id, {"type": "complete"}).json()["operation"]["status"], "completed")

        history = self.call("get", f"/operations/{self.cut_id}/events").json()
        self.assertEqual([e["type"] for e in history], ["start", "hold", "release", "resume", "complete"])

    def test_invalid_move_is_409_and_records_nothing(self):
        response = self.event(self.cut_id, {"type": "complete"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Cannot complete", response.json()["detail"])
        self.assertEqual(self.event(self.cut_id, {"type": "hold"}).status_code, 409)
        self.assertEqual(self.event(self.cut_id, {"type": "note"}).status_code, 422)
        self.assertEqual(self.event(self.cut_id, {"type": "explode"}).status_code, 422)
        self.assertFalse(Event.objects.exists())

    def test_rework_and_skip_need_office_role(self):
        self.assertEqual(self.event(self.weld_id, {"type": "skip"}).status_code, 403)
        self.assertEqual(self.event(self.weld_id, {"type": "skip"}, key=self.office_key).json()["operation"]["status"], "skipped")
        self.event(self.cut_id, {"type": "start"})
        self.event(self.cut_id, {"type": "complete"})
        self.assertEqual(self.event(self.cut_id, {"type": "rework", "note": "Short"}).status_code, 403)
        reworked = self.event(self.cut_id, {"type": "rework", "note": "Short"}, key=self.office_key).json()
        self.assertEqual((reworked["operation"]["status"], reworked["operation"]["rework_count"]), ("pending", 1))

    def test_backdated_event(self):
        response = self.event(self.cut_id, {"type": "start", "occurred_at": "2026-11-01T08:30:00Z"}).json()
        self.assertTrue(response["operation"]["started_at"].startswith("2026-11-01T08:30:00"))

    def test_event_feed_cursor(self):
        self.event(self.cut_id, {"type": "start"})
        self.event(self.cut_id, {"type": "complete"})
        self.event(self.weld_id, {"type": "start"})
        feed = self.call("get", "/events?limit=2").json()
        self.assertEqual([e["type"] for e in feed], ["start", "complete"])
        rest = self.call("get", f"/events?after_id={feed[-1]['id']}").json()
        self.assertEqual([(e["type"], e["operation_name"], e["job_reference"]) for e in rest], [("start", "Weld", "J-1")])
        self.assertEqual(len(self.call("get", "/events?type=complete").json()), 1)

    def test_operations_list_and_queue(self):
        self.assertEqual(self.call("get", f"/operations?work_centre_id={self.cut.pk}").json()["count"], 1)
        self.assertEqual(self.call("get", "/operations?status=pending").json()["count"], 2)
        queue = self.call("get", f"/work-centres/{self.weld.pk}/queue").json()
        self.assertEqual([op["name"] for op in queue["upcoming"]], ["Weld"])
        self.assertEqual(queue["upcoming"][0]["waiting_on"], ["Cut"])
        self.assertFalse(queue["upcoming"][0]["can_start"])
        queue = self.call("get", f"/work-centres/{self.cut.pk}/queue").json()
        self.assertEqual([op["name"] for op in queue["ready"]], ["Cut"])

    def test_issues(self):
        raised = self.call("post", f"/operations/{self.cut_id}/issues", {"description": "5mm short", "category": "ncr"}, key=self.operator_key)
        self.assertEqual(raised.status_code, 201)
        issue_id = raised.json()["id"]
        self.assertEqual(self.call("get", "/issues?open=true").json()["count"], 1)
        self.assertEqual(self.call("post", f"/issues/{issue_id}/resolve", {"resolution": "Recut"}, key=self.operator_key).status_code, 403)
        resolved = self.call("post", f"/issues/{issue_id}/resolve", {"resolution": "Recut"}).json()
        self.assertEqual((resolved["is_open"], resolved["resolution"], resolved["resolved_by"]), (False, "Recut", "erp"))
        self.assertEqual(self.call("get", "/issues?open=true").json()["count"], 0)
        self.assertEqual(Issue.objects.get().raised_by, self.operator)


class ParallelRouteApiTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        GateType.objects.create(name="Hardware")

    def make_branching_job(self):
        job = self.make_job(items=[{
            "name": "Folded panel",
            "operations": [
                {"name": "Program", "work_centre": "CUT", "after": []},
                {"name": "Cut", "work_centre": "CUT"},
                {"name": "Fold", "work_centre": "WLD"},
                {"name": "Glue", "work_centre": "WLD", "after": ["Fold"]},
                {"name": "Build", "work_centre": "WLD", "needs": ["Hardware"],
                 "after": ["Fold", {"step": "Glue", "type": "finish_to_finish"}]},
                {"name": "Finish", "work_centre": "CUT"},
            ],
        }])
        item = job["items"][0]
        return item, {op["name"]: op for op in item["operations"]}

    def event(self, operation, type_):
        return self.call("post", f"/operations/{operation['id']}/events", {"type": type_})

    def do(self, operation):
        self.assertEqual(self.event(operation, "start").status_code, 201)
        self.assertEqual(self.event(operation, "complete").status_code, 201)

    def test_branching_route_created_in_one_call(self):
        item, ops = self.make_branching_job()
        self.assertEqual(ops["Program"]["after"], [])
        self.assertTrue(ops["Program"]["can_start"])
        self.assertEqual([link["name"] for link in ops["Cut"]["after"]], ["Program"])
        self.assertEqual([(l["name"], l["type"], l["enforcement"]) for l in ops["Build"]["after"]],
                         [("Fold", "finish_to_start", "strict"), ("Glue", "finish_to_finish", "strict")])
        self.assertEqual([(n["name"], n["is_cleared"]) for n in ops["Build"]["needs"]], [("Hardware", False)])
        self.assertEqual((ops["Build"]["waiting_on"], ops["Build"]["waiting_gates"], ops["Build"]["finish_waiting"]),
                         (["Fold"], ["Hardware"], ["Glue"]))
        self.assertEqual([(g["name"], g["needed_by"]) for g in item["gates"]], [("Hardware", [ops["Build"]["id"]])])

    def test_gate_holds_build_until_cleared_through_the_api(self):
        item, ops = self.make_branching_job()
        for name in ("Program", "Cut", "Fold"):
            self.do(ops[name])
        self.assertEqual(self.event(ops["Glue"], "start").status_code, 201)

        blocked = self.event(ops["Build"], "start")
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("Waiting for: Hardware", blocked.json()["detail"])
        self.assertEqual(self.call("get", "/board/items?status=gate").json()["items"][0]["gates_waiting"], ["Hardware"])

        waiting = self.call("get", "/gates?cleared=false&gate_type=hardware").json()
        self.assertEqual(len(waiting), 1)
        gate_id = waiting[0]["id"]
        self.assertEqual(self.call("post", f"/gates/{gate_id}/clear", {"note": "x"}, key=self.operator_key).status_code, 403)
        self.assertEqual(self.call("patch", f"/gates/{gate_id}", {"expected_date": "2026-12-01"}).json()["expected_date"], "2026-12-01")
        cleared = self.call("post", f"/gates/{gate_id}/clear", {"note": "Delivered"}).json()
        self.assertEqual((cleared["is_cleared"], cleared["cleared_by"]), (True, "erp"))

        self.assertEqual(self.event(ops["Build"], "start").status_code, 201)
        early = self.event(ops["Build"], "complete")
        self.assertEqual(early.status_code, 409)
        self.assertIn("Cannot complete until done: Glue", early.json()["detail"])
        self.event(ops["Glue"], "complete")
        self.assertEqual(self.event(ops["Build"], "complete").status_code, 201)

        row = self.call("get", "/board/items").json()["items"][0]
        self.assertEqual(row["gates_waiting"], [])

    def test_two_steps_active_at_once_show_on_the_board(self):
        _, ops = self.make_branching_job()
        gate_id = self.call("get", "/gates").json()[0]["id"]
        self.call("post", f"/gates/{gate_id}/clear", {})
        for name in ("Program", "Cut", "Fold"):
            self.do(ops[name])
        self.event(ops["Glue"], "start")
        self.event(ops["Build"], "start")
        row = self.call("get", "/board/items").json()["items"][0]
        self.assertEqual(sorted(op["name"] for op in row["active_operations"]), ["Build", "Glue"])

    def test_link_endpoints_and_rules(self):
        _, ops = self.make_branching_job()
        finish, program, build = ops["Finish"]["id"], ops["Program"]["id"], ops["Build"]["id"]

        added = self.call("post", f"/operations/{finish}/links", {"after": program, "enforcement": "advisory"}).json()
        self.assertIn(("Program", "advisory"), [(l["name"], l["enforcement"]) for l in added["after"]])
        patched = self.call("patch", f"/operations/{finish}/links/{program}", {"type": "finish_to_finish"}).json()
        self.assertIn(("Program", "finish_to_finish"), [(l["name"], l["type"]) for l in patched["after"]])
        removed = self.call("delete", f"/operations/{finish}/links/{program}").json()
        self.assertEqual([l["name"] for l in removed["after"]], ["Build"])

        loop = self.call("post", f"/operations/{program}/links", {"after": build})
        self.assertEqual(loop.status_code, 409)
        self.assertIn("loop", loop.json()["detail"])
        self.assertEqual(self.call("delete", f"/operations/{finish}/links/{program}").status_code, 404)
        self.assertEqual(self.call("post", f"/operations/{finish}/links", {"after": program}, key=self.operator_key).status_code, 403)

    def test_gate_requirement_endpoints_sequence_and_template(self):
        item, ops = self.make_branching_job()
        cut = ops["Cut"]["id"]
        needs = self.call("post", f"/operations/{cut}/gates", {"gate_type": "hardware"}).json()["needs"]
        self.assertEqual([n["name"] for n in needs], ["Hardware"])
        self.assertEqual(self.call("delete", f"/operations/{cut}/gates/{needs[0]['gate_id']}").json()["needs"], [])
        self.assertEqual(self.call("post", f"/operations/{cut}/gates", {"gate_type": "Unobtainium"}).status_code, 422)

        template = self.call("post", f"/items/{item['id']}/route/save-template", {"name": "Folded panel"})
        self.assertEqual(template.status_code, 201)
        self.assertEqual((template.json()["simple_sequence"], len(template.json()["links"])), (False, 6))
        self.assertEqual(template.json()["steps"][4]["needs"], ["Hardware"])

        chain = self.call("post", f"/items/{item['id']}/route/sequence", {"enforcement": "advisory"}).json()
        self.assertEqual([[l["name"] for l in op["after"]] for op in chain],
                         [[], ["Program"], ["Cut"], ["Fold"], ["Glue"], ["Build"]])
        self.assertEqual(self.call("patch", f"/items/{item['id']}", {"link_enforcement": "advisory"}).json()["link_enforcement"], "advisory")

    def test_bad_step_reference_saves_nothing(self):
        response = self.call("post", "/jobs", {"reference": "J-7", "title": "Bad", "customer": "Acme", "items": [{
            "name": "X", "operations": [{"name": "A", "work_centre": "CUT", "after": ["Missing"]}],
        }]})
        self.assertEqual(response.status_code, 409)
        self.assertIn("not an earlier step", response.json()["detail"])
        self.assertFalse(Job.objects.filter(reference="J-7").exists())

    def test_added_step_follows_the_last_one_unless_told_otherwise(self):
        item, ops = self.make_branching_job()
        default = self.call("post", f"/items/{item['id']}/operations", {"name": "Pack", "work_centre": "CUT"}).json()
        self.assertEqual([l["name"] for l in default["after"]], ["Finish"])
        free = self.call("post", f"/items/{item['id']}/operations", {"name": "Label", "work_centre": "CUT", "after": []}).json()
        self.assertEqual((free["after"], free["can_start"]), ([], True))
        by_id = self.call("post", f"/items/{item['id']}/operations",
                          {"name": "Wrap", "work_centre": "CUT", "after": [ops["Glue"]["id"], "Pack"]}).json()
        self.assertEqual(sorted(l["name"] for l in by_id["after"]), ["Glue", "Pack"])


class BoardTests(ApiTestCase):
    def test_board_items_and_lanes(self):
        item = self.make_job(due_date="2020-01-01")["items"][0]
        cut_id = item["operations"][0]["id"]
        self.call("post", f"/operations/{cut_id}/events", {"type": "hold", "hold_reason_id": self.reason.pk})

        board = self.call("get", "/board/items").json()
        self.assertEqual(board["summary"], {"active": 1, "in_progress": 0, "on_hold": 1, "late": 1, "at_risk": 0})
        row = board["items"][0]
        self.assertEqual((row["job_reference"], row["state"], row["is_late"], row["steps_total"]), ("J-1", "on_hold", True, 2))
        self.assertEqual(row["current_operation"]["id"], cut_id)
        self.assertIsNotNone(row["held_since"])
        self.assertEqual(self.call("get", "/board/items?status=in_progress").json()["items"], [])
        self.assertEqual(self.call("get", "/board/items?status=bogus").status_code, 422)

        lanes = {lane["work_centre"]["code"]: lane for lane in self.call("get", "/board/work-centres").json()}
        self.assertEqual([op["name"] for op in lanes["CUT"]["on_hold"]], ["Cut"])
        self.assertEqual([op["name"] for op in lanes["WLD"]["upcoming"]], ["Weld"])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AttachmentTests(ApiTestCase):
    def upload(self, path="/attachments", key=None, **fields):
        fields.setdefault("file", SimpleUploadedFile("drawing.pdf", b"%PDF-1.4 x", content_type="application/pdf"))
        return self.client.post(f"/api/v1{path}", fields, headers={"Authorization": f"Bearer {key or self.office_key}"})

    def test_upload_list_supersede_archive(self):
        job = self.make_job()
        item_id = job["items"][0]["id"]
        uploaded = self.upload(target_type="item", target_id=item_id, category="drawing", revision="A", key=self.operator_key)
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        first = uploaded.json()
        self.assertEqual((first["target_type"], first["filename"], first["revision"], first["uploaded_by"]),
                         ("item", "drawing.pdf", "A", "scanner"))
        self.assertTrue(first["url"].startswith("http://testserver/media/attachments/jobitem/"))

        self.assertEqual(self.upload(f"/attachments/{first['id']}/supersede", revision="B", key=self.operator_key).status_code, 403)
        second = self.upload(f"/attachments/{first['id']}/supersede", revision="B")
        self.assertEqual(second.status_code, 201)
        self.assertEqual(self.upload(f"/attachments/{first['id']}/supersede", revision="C").status_code, 409)

        current = self.call("get", f"/attachments?target_type=item&target_id={item_id}").json()
        self.assertEqual([a["revision"] for a in current], ["B"])
        everything = self.call("get", f"/attachments?target_type=item&target_id={item_id}&include_history=true").json()
        self.assertEqual(len(everything), 2)

        self.assertTrue(self.call("post", f"/attachments/{second.json()['id']}/archive").json()["is_archived"])
        self.assertEqual(self.call("get", f"/attachments?target_type=item&target_id={item_id}").json(), [])
        self.assertEqual(Attachment.objects.count(), 2)

    def test_rejects_bad_type_category_and_target(self):
        job = self.make_job()
        bad_file = SimpleUploadedFile("run.exe", b"MZ")
        self.assertEqual(self.upload(target_type="job", target_id=job["id"], file=bad_file).status_code, 422)
        self.assertEqual(self.upload(target_type="job", target_id=job["id"], category="selfie").status_code, 422)
        self.assertEqual(self.upload(target_type="user", target_id=1).status_code, 422)
        self.assertEqual(self.upload(target_type="job", target_id=99999).status_code, 404)
        self.assertFalse(Attachment.objects.exists())

    def test_photo_on_an_issue(self):
        item = self.make_job()["items"][0]
        issue = self.call("post", f"/operations/{item['operations'][0]['id']}/issues", {"description": "Dent"}).json()
        photo = SimpleUploadedFile("dent.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
        response = self.upload(target_type="issue", target_id=issue["id"], category="photo", file=photo)
        self.assertEqual((response.status_code, response.json()["target_type"]), (201, "issue"))
