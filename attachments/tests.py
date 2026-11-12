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
