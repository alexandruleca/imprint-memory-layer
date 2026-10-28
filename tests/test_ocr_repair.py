import os
import unittest
from unittest.mock import Mock, patch

from imprint.extractors import OcrRuntimeMissing, ExtractorUnavailable, ExtractionError, _with_ocr_repair


class OcrRepairTests(unittest.TestCase):
    def test_managed_worker_requests_repair_without_installing_in_process(self):
        extract = Mock(side_effect=OcrRuntimeMissing('missing RapidOCR'))
        with patch.dict(os.environ, {'IMPRINT_OCR_REPAIR_REQUEST': '1'}):
            with self.assertRaises(SystemExit) as result:
                _with_ocr_repair(extract)
        self.assertEqual(result.exception.code, 75)
        extract.assert_called_once()

    def test_retry_or_unmanaged_worker_reports_missing_runtime(self):
        for value in ['', '0']:
            with patch.dict(os.environ, {'IMPRINT_OCR_REPAIR_REQUEST': value}):
                with self.assertRaisesRegex(OcrRuntimeMissing, 'missing'):
                    _with_ocr_repair(Mock(side_effect=OcrRuntimeMissing('missing')))

    def test_opt_out_and_other_failures_do_not_request_bootstrap(self):
        with patch.dict(os.environ, {'IMPRINT_OCR_REPAIR_REQUEST': '1'}):
            for error in [ExtractorUnavailable('image OCR disabled'),
                          ExtractorUnavailable('Custom OCR languages need Tesseract'),
                          ExtractionError('invalid image')]:
                with self.assertRaises(type(error)):
                    _with_ocr_repair(Mock(side_effect=error))

    def test_success_and_no_text_pass_through(self):
        for text in ['readable text', '']:
            with patch.dict(os.environ, {'IMPRINT_OCR_REPAIR_REQUEST': '1'}):
                self.assertEqual(_with_ocr_repair(lambda: text), text)
