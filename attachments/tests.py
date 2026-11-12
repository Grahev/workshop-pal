import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from jobs.models import Customer, Job
from resources.models import Site

from . import services
from .models import Attachment


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AttachmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Main workshop")
        customer = Customer.objects.create(name="Acme")
        cls.job = Job.objects.create(reference="J-001", customer=customer, site=site, title="Frame")

    def pdf(self, name="drawing.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")

    def test_attach_to_any_record(self):
        attachment = services.attach(self.job, self.pdf(), category=Attachment.Category.DRAWING, revision="A")
        self.assertEqual(attachment.parent, self.job)
        self.assertEqual(attachment.original_filename, "drawing.pdf")
        self.assertEqual(list(services.attachments_for(self.job)), [attachment])

    def test_new_revision_supersedes_old(self):
        rev_a = services.attach(self.job, self.pdf(), category=Attachment.Category.DRAWING, revision="A")
        rev_b = services.supersede(rev_a, self.pdf("drawing_b.pdf"), revision="B")

        rev_a.refresh_from_db()
        self.assertFalse(rev_a.is_current)
        self.assertEqual(rev_b.supersedes, rev_a)
        self.assertEqual(list(services.attachments_for(self.job)), [rev_b])
        self.assertEqual(services.attachments_for(self.job, include_history=True).count(), 2)

    def test_disallowed_file_type_rejected(self):
        bad = SimpleUploadedFile("script.exe", b"MZ")
        with self.assertRaises(ValidationError):
            services.attach(self.job, bad)

    def test_attachments_cannot_be_deleted(self):
        attachment = services.attach(self.job, self.pdf())
        with self.assertRaises(TypeError):
            attachment.delete()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AttachmentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType

        User = get_user_model()
        cls.office = User.objects.create_user("office", password="x", role="office")
        cls.operator = User.objects.create_user("op", password="x", role="operator")
        site = Site.objects.create(name="Main workshop")
        customer = Customer.objects.create(name="Acme")
        cls.job = Job.objects.create(reference="J-001", customer=customer, site=site, title="Frame")
        cls.job_ct = ContentType.objects.get_for_model(Job).pk
        cls.user_ct = ContentType.objects.get_for_model(User).pk

    def pdf(self, name="drawing.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")

    def upload(self, **extra):
        data = {"content_type": self.job_ct, "object_id": self.job.pk, "file": self.pdf(), "next": f"/jobs/{self.job.pk}/"}
        data.update(extra)
        return self.client.post("/files/upload/", data)

    def test_operator_can_upload_and_it_shows_on_the_job(self):
        self.client.force_login(self.operator)
        response = self.upload(category="drawing", revision="A")
        self.assertRedirects(response, f"/jobs/{self.job.pk}/")
        attachment = Attachment.objects.get()
        self.assertEqual((attachment.category, attachment.revision, attachment.uploaded_by), ("drawing", "A", self.operator))
        self.assertContains(self.client.get(f"/jobs/{self.job.pk}/"), "drawing.pdf")

    def test_bad_file_type_is_rejected_with_message(self):
        self.client.force_login(self.operator)
        response = self.upload(file=SimpleUploadedFile("x.exe", b"MZ"))
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertContains(self.client.get(response["Location"]), "not allowed")

    def test_cannot_attach_to_other_models(self):
        self.client.force_login(self.operator)
        response = self.upload(content_type=self.user_ct, object_id=self.operator.pk)
        self.assertEqual(response.status_code, 400)

    def test_supersede_and_archive_are_office_only(self):
        rev_a = services.attach(self.job, self.pdf(), category=Attachment.Category.DRAWING, revision="A")
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(f"/files/{rev_a.pk}/supersede/", {"revision": "B", "file": self.pdf()}).status_code, 403)
        self.assertEqual(self.client.post(f"/files/{rev_a.pk}/archive/").status_code, 403)

        self.client.force_login(self.office)
        self.client.post(f"/files/{rev_a.pk}/supersede/", {"revision": "B", "file": self.pdf("b.pdf"), "next": f"/jobs/{self.job.pk}/"})
        rev_b = Attachment.objects.get(revision="B")
        self.assertEqual(list(services.attachments_for(self.job)), [rev_b])

        self.client.post(f"/files/{rev_b.pk}/archive/")
        self.assertEqual(list(services.attachments_for(self.job)), [])
        self.assertEqual(Attachment.objects.count(), 2)
