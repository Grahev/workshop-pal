from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from floor import services as floor
from floor.services import InvalidTransition
from jobs.models import Customer, Job, JobItem
from resources.models import Site, WorkCentre

from . import services
from .models import Dependency, Enforcement, GateType, LinkKind, Operation, RouteTemplate
from .services import RouteError

FF = LinkKind.FINISH_TO_FINISH
ADVISORY = Enforcement.ADVISORY
STRICT = Enforcement.STRICT
Status = Operation.Status


class RouteTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.office = User.objects.create_user("office", password="x", role="office")
        cls.operator = User.objects.create_user("op", password="x", role="operator")
        cls.site = Site.objects.create(name="Main")
        cls.wc = WorkCentre.objects.create(site=cls.site, name="Shop", code="SHOP")
        cls.hardware = GateType.objects.create(name="Hardware")
        cls.customer = Customer.objects.create(name="Acme")
        cls.job = Job.objects.create(reference="J-1", customer=cls.customer, site=cls.site, title="Panel")

    def make_item(self, name="Panel"):
        return JobItem.objects.create(job=self.job, name=name)

    def step(self, name, after=None, needs=()):
        step = {"name": name, "work_centre": self.wc, "gate_types": list(needs)}
        if after is not None:
            step["after"] = after
        return step

    def build_example(self, build_after_glue=None):
        """Program -> Cut -> Fold, then Glue and Build branch from Fold; Glue feeds Build; Build needs hardware."""
        item = self.make_item()
        services.add_steps(item, [
            self.step("Program", after=[]),
            self.step("Cut"),
            self.step("Fold"),
            self.step("Glue", after=[("Fold", None, None)]),
            self.step("Build", after=[("Fold", None, None), ("Glue", build_after_glue, None)], needs=[self.hardware]),
            self.step("Finish"),
        ])
        return item, {op.name: op for op in item.operations.all()}

    def do(self, operation):
        floor.start_operation(operation)
        floor.complete_operation(operation)

    def can_start(self, operation):
        return Operation.objects.get(pk=operation.pk).readiness().can_start


class ParallelRouteTests(RouteTestCase):
    def test_only_the_first_step_can_start(self):
        _, ops = self.build_example()
        self.assertEqual([name for name, op in ops.items() if self.can_start(op)], ["Program"])
        with self.assertRaisesMessage(InvalidTransition, "Cannot start until done: Program"):
            floor.start_operation(ops["Cut"])

    def test_branches_open_together_and_join_again(self):
        item = self.make_item()
        services.add_steps(item, [
            self.step("Fold", after=[]),
            self.step("Glue", after=[("Fold", None, None)]),
            self.step("Drill", after=[("Fold", None, None)]),
            self.step("Build", after=[("Glue", None, None), ("Drill", None, None)]),
        ])
        ops = {op.name: op for op in item.operations.all()}
        self.do(ops["Fold"])
        # Both branches are open at once.
        floor.start_operation(ops["Glue"])
        floor.start_operation(ops["Drill"])
        floor.complete_operation(ops["Glue"])
        # The join waits for every branch.
        with self.assertRaisesMessage(InvalidTransition, "Cannot start until done: Drill"):
            floor.start_operation(ops["Build"])
        floor.complete_operation(ops["Drill"])
        floor.start_operation(ops["Build"])

    def test_gate_blocks_only_the_step_that_needs_it(self):
        item, ops = self.build_example()
        for name in ("Program", "Cut", "Fold", "Glue"):
            self.do(ops[name])  # none of these care that hardware is missing
        with self.assertRaisesMessage(InvalidTransition, "Waiting for: Hardware"):
            floor.start_operation(ops["Build"])

        gate = item.gates.get()
        services.clear_gate(gate, user=self.office, note="Delivered")
        floor.start_operation(ops["Build"])
        self.assertIn("Hardware available. Delivered", [e.note for e in ops["Build"].events.all()])

    def test_finish_to_finish_lets_work_start_but_not_complete(self):
        _, ops = self.build_example(build_after_glue=FF)
        item = ops["Build"].item
        services.clear_gate(item.gates.get())
        for name in ("Program", "Cut", "Fold"):
            self.do(ops[name])
        floor.start_operation(ops["Glue"])
        floor.start_operation(ops["Build"])  # starts on the other parts while glue is still running
        with self.assertRaisesMessage(InvalidTransition, "Cannot complete until done: Glue"):
            floor.complete_operation(ops["Build"])
        floor.complete_operation(ops["Glue"])
        floor.complete_operation(ops["Build"])

    def test_advisory_link_warns_but_does_not_block(self):
        item = self.make_item()
        services.add_steps(item, [self.step("Cut", after=[]), self.step("Fold", after=[("Cut", None, ADVISORY)])])
        fold = item.operations.get(name="Fold")
        readiness = fold.readiness()
        self.assertEqual((readiness.can_start, readiness.warnings), (True, ["Cut"]))
        floor.start_operation(fold)

    def test_skipped_step_counts_as_done(self):
        _, ops = self.build_example()
        self.do(ops["Program"])
        floor.skip_operation(ops["Cut"])
        self.assertTrue(self.can_start(ops["Fold"]))

    def test_item_default_decides_enforcement_of_new_links(self):
        item = self.make_item()
        services.set_default_enforcement(item, ADVISORY)
        services.add_steps(item, [self.step("A", after=[]), self.step("B")])
        self.assertEqual(Dependency.objects.get().enforcement, ADVISORY)

    def test_stages_show_what_runs_side_by_side(self):
        item, _ = self.build_example()
        stages = services.route_stages(services.route_operations(item))
        self.assertEqual([[op.name for op in stage] for stage in stages],
                         [["Program"], ["Cut"], ["Fold"], ["Glue"], ["Build"], ["Finish"]])

        # Build may start alongside Glue when it only has to finish after it.
        item, _ = self.build_example(build_after_glue=FF)
        stages = services.route_stages(services.route_operations(item))
        self.assertEqual([[op.name for op in stage] for stage in stages],
                         [["Program"], ["Cut"], ["Fold"], ["Glue", "Build"], ["Finish"]])

        item = self.make_item("Wide")
        services.add_steps(item, [
            self.step("Fold", after=[]), self.step("Glue"), self.step("Drill", after=[("Fold", None, None)]),
            self.step("Build", after=[("Glue", None, None), ("Drill", None, None)]),
        ])
        stages = services.route_stages(services.route_operations(item))
        self.assertEqual([[op.name for op in stage] for stage in stages], [["Fold"], ["Glue", "Drill"], ["Build"]])


class RouteRuleTests(RouteTestCase):
    def test_loops_self_links_and_other_items_refused(self):
        _, ops = self.build_example()
        with self.assertRaisesMessage(RouteError, "loop"):
            services.add_link(ops["Program"], ops["Build"])
        with self.assertRaisesMessage(RouteError, "itself"):
            services.add_link(ops["Cut"], ops["Cut"])
        other = self.make_item("Other")
        stranger = services.add_operation(other, "X", self.wc)
        with self.assertRaisesMessage(RouteError, "same item"):
            services.add_link(ops["Cut"], stranger)

    def test_unknown_step_name_refused_and_nothing_saved(self):
        item = self.make_item()
        with self.assertRaisesMessage(RouteError, "not an earlier step"):
            services.add_steps(item, [self.step("A", after=[]), self.step("B", after=[("Nope", None, None)])])
        self.assertFalse(item.operations.exists())

    def test_started_step_cannot_be_tightened_but_can_be_relaxed(self):
        item = self.make_item()
        a, b, c = services.add_steps(item, [self.step("A", after=[]), self.step("B", after=[]), self.step("C", after=[])])
        floor.start_operation(b)
        with self.assertRaisesMessage(RouteError, "already started"):
            services.add_link(b, a, enforcement=STRICT)
        with self.assertRaisesMessage(RouteError, "already started"):
            services.require_gate(b, self.hardware)
        link = services.add_link(b, a, enforcement=ADVISORY)
        with self.assertRaisesMessage(RouteError, "already started"):
            services.update_link(link, enforcement=STRICT)
        # A step that has not started can be tightened freely.
        services.update_link(services.add_link(c, a, enforcement=ADVISORY), enforcement=STRICT)
        services.require_gate(c, self.hardware)

    def test_removing_a_step_joins_its_neighbours(self):
        _, ops = self.build_example()
        services.remove_operation(ops["Cut"])
        self.assertEqual([link.predecessor.name for link in ops["Fold"].dependencies.all()], ["Program"])

    def test_simple_sequence_shortcut(self):
        item, ops = self.build_example()
        services.set_simple_sequence(item, ADVISORY)
        links = [(link.operation.name, link.predecessor.name, link.enforcement) for link in
                 Dependency.objects.filter(operation__item=item).order_by("operation__sequence")]
        self.assertEqual(links, [("Cut", "Program", "advisory"), ("Fold", "Cut", "advisory"), ("Glue", "Fold", "advisory"),
                                 ("Build", "Glue", "advisory"), ("Finish", "Build", "advisory")])

        item2 = self.make_item("Started")
        a, b = services.add_steps(item2, [self.step("A", after=[]), self.step("B", after=[])])
        floor.start_operation(b)
        with self.assertRaises(RouteError):
            services.set_simple_sequence(item2, STRICT)
        services.set_simple_sequence(item2, ADVISORY)

    def test_reopening_a_gate_does_not_stop_started_work(self):
        item, ops = self.build_example()
        gate = item.gates.get()
        services.clear_gate(gate)
        for name in ("Program", "Cut", "Fold", "Glue"):
            self.do(ops[name])
        floor.start_operation(ops["Build"])
        services.reopen_gate(gate)
        floor.complete_operation(ops["Build"])
        self.assertTrue(self.can_start(ops["Finish"]))


class TemplateTests(RouteTestCase):
    def test_route_saved_as_template_and_applied_keeps_links_and_gates(self):
        item, _ = self.build_example(build_after_glue=FF)
        template = services.save_as_template(item, "Folded panel")
        self.assertFalse(template.simple_sequence)

        copy = self.make_item("Copy")
        services.apply_template(copy, template)
        ops = {op.name: op for op in services.route_operations(copy)}
        links = {name: sorted((l.predecessor.name, l.kind) for l in op.dependencies.all()) for name, op in ops.items()}
        self.assertEqual(links["Program"], [])
        self.assertEqual(links["Glue"], [("Fold", "finish_to_start")])
        self.assertEqual(links["Build"], [("Fold", "finish_to_start"), ("Glue", "finish_to_finish")])
        self.assertEqual([g.gate_type.name for g in ops["Build"].gates.all()], ["Hardware"])
        self.assertFalse(copy.gates.get().is_cleared)

    def test_simple_sequence_template_chains_steps_with_its_default(self):
        item, _ = self.build_example()
        template = services.save_as_template(item, "As chain")
        template.simple_sequence = True
        template.default_enforcement = ADVISORY
        template.save()
        copy = self.make_item("Copy")
        services.apply_template(copy, template)
        links = Dependency.objects.filter(operation__item=copy)
        self.assertEqual(links.count(), 5)
        self.assertEqual(set(links.values_list("enforcement", flat=True)), {"advisory"})
        copy.refresh_from_db()
        self.assertEqual(copy.link_enforcement, ADVISORY)


class RouteBuilderViewTests(RouteTestCase):
    def setUp(self):
        self.client.force_login(self.office)
        self.item, self.ops = self.build_example()

    def test_item_page_shows_links_gates_and_flow(self):
        response = self.client.get(f"/jobs/items/{self.item.pk}/")
        self.assertContains(response, "After Fold")
        self.assertContains(response, "Needs Hardware")
        self.assertContains(response, "How it flows")
        self.assertContains(response, "Mark available")

    def test_add_step_with_links_and_gate(self):
        self.client.post(f"/jobs/items/{self.item.pk}/route/add/", {
            "name": "Pack", "work_centre": self.wc.pk, "estimated_minutes": 5,
            "after": [self.ops["Finish"].pk, self.ops["Glue"].pk], "needs": [self.hardware.pk],
        })
        pack = self.item.operations.get(name="Pack")
        self.assertEqual(sorted(l.predecessor.name for l in pack.dependencies.all()), ["Finish", "Glue"])
        self.assertEqual(pack.gates.count(), 1)

    def test_step_editor_links(self):
        finish, program, build = self.ops["Finish"], self.ops["Program"], self.ops["Build"]
        self.assertContains(self.client.get(f"/jobs/operations/{finish.pk}/edit/"), "Comes after")
        self.client.post(f"/jobs/operations/{finish.pk}/links/add/", {"predecessor": program.pk, "kind": FF, "enforcement": ADVISORY})
        link = finish.dependencies.get(predecessor=program)
        self.assertEqual((link.kind, link.enforcement), (FF, ADVISORY))

        self.client.post(f"/jobs/links/{link.pk}/update/", {"kind": "finish_to_start", "enforcement": STRICT})
        link.refresh_from_db()
        self.assertEqual((link.kind, link.enforcement), ("finish_to_start", STRICT))

        response = self.client.post(f"/jobs/operations/{program.pk}/links/add/", {"predecessor": build.pk}, follow=True)
        self.assertContains(response, "loop")

        self.client.post(f"/jobs/links/{link.pk}/remove/")
        self.assertFalse(finish.dependencies.filter(predecessor=program).exists())

    def test_set_needs_and_clear_gate(self):
        cut = self.ops["Cut"]
        self.client.post(f"/jobs/operations/{cut.pk}/gates/", {"needs": [self.hardware.pk]})
        self.assertEqual(cut.gates.count(), 1)
        self.client.post(f"/jobs/operations/{cut.pk}/gates/", {})
        self.assertEqual(cut.gates.count(), 0)

        gate = self.item.gates.get()
        self.client.post(f"/jobs/gates/{gate.pk}/expected/", {"expected_date": "2026-12-01"})
        gate.refresh_from_db()
        self.assertEqual(str(gate.expected_date), "2026-12-01")
        self.client.post(f"/jobs/gates/{gate.pk}/clear/", {"note": "In stores"})
        gate.refresh_from_db()
        self.assertEqual((gate.is_cleared, gate.cleared_by, gate.note), (True, self.office, "In stores"))
        self.client.post(f"/jobs/gates/{gate.pk}/reopen/")
        gate.refresh_from_db()
        self.assertFalse(gate.is_cleared)

    def test_route_settings(self):
        self.client.post(f"/jobs/items/{self.item.pk}/route/enforcement/", {"enforcement": ADVISORY})
        self.item.refresh_from_db()
        self.assertEqual(self.item.link_enforcement, ADVISORY)
        self.client.post(f"/jobs/items/{self.item.pk}/route/sequence/", {"enforcement": STRICT})
        self.assertEqual(Dependency.objects.filter(operation__item=self.item).count(), 5)
        self.client.post(f"/jobs/items/{self.item.pk}/route/save-template/", {"name": "Saved"})
        self.assertEqual(RouteTemplate.objects.get(name="Saved").steps.count(), 6)

    def test_operator_cannot_change_route_or_gates(self):
        self.client.force_login(self.operator)
        gate = self.item.gates.get()
        self.assertEqual(self.client.post(f"/jobs/gates/{gate.pk}/clear/").status_code, 403)
        self.assertEqual(self.client.post(f"/jobs/operations/{self.ops['Cut'].pk}/links/add/", {"predecessor": 1}).status_code, 403)
        self.assertEqual(self.client.post(f"/jobs/items/{self.item.pk}/route/sequence/").status_code, 403)

    def test_workstation_shows_what_a_step_is_waiting_for(self):
        for name in ("Program", "Cut", "Fold", "Glue"):
            self.do(self.ops[name])
        self.client.force_login(self.operator)
        response = self.client.get(f"/floor/station/{self.wc.pk}/")
        self.assertContains(response, "Waiting for: Hardware")
        build = next(op for op in response.context["queue"]["upcoming"] if op.name == "Build")
        self.assertEqual((build.waiting_on, build.waiting_gates, build.can_start), ([], ["Hardware"], False))


class ConvertExistingRoutesMigrationTests(TransactionTestCase):
    """Routes made before links existed become sequences that behave as they did."""

    def test_old_routes_become_advisory_sequences_and_hard_dependencies_stay_strict(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("routing", "0001_initial"), ("jobs", "0001_initial")])
        old = executor.loader.project_state([("routing", "0001_initial"), ("jobs", "0001_initial")]).apps

        site = old.get_model("resources", "Site").objects.create(name="Main")
        wc = old.get_model("resources", "WorkCentre").objects.create(site=site, name="Shop", code="SHOP")
        customer = old.get_model("jobs", "Customer").objects.create(name="Acme")
        job = old.get_model("jobs", "Job").objects.create(reference="J-1", customer=customer, site=site, title="Old")
        item = old.get_model("jobs", "JobItem").objects.create(job=job, name="Old item")
        OldOperation = old.get_model("routing", "Operation")
        a, b, c = [OldOperation.objects.create(item=item, sequence=n, name=name, work_centre=wc)
                   for n, name in ((10, "A"), (20, "B"), (30, "C"))]
        c.depends_on.add(a)  # a hard dependency set by hand
        old.get_model("routing", "RouteTemplate").objects.create(name="Old template")

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

        links = {(l.operation.name, l.predecessor.name): l.enforcement for l in Dependency.objects.all()}
        self.assertEqual(links, {("B", "A"): "advisory", ("C", "B"): "advisory", ("C", "A"): "strict"})
        self.assertEqual(JobItem.objects.get().link_enforcement, "advisory")
        template = RouteTemplate.objects.get()
        self.assertEqual((template.simple_sequence, template.default_enforcement), (True, "advisory"))
