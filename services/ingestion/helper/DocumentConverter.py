"""Format conversion helper for the document ingestion pipeline.

Converts office and document files to PDF using LibreOffice headless so that
PyMuPDF and the Vision LLM can process them uniformly.  Native formats (PDF,
images, plain text) are copied to the working directory without conversion.
"""
import asyncio
import logging
import os
import shutil
from shared.helper.HelperFile import HelperFile
from uuid import uuid4

from shared.helper.HelperConfig import HelperConfig

class DocumentConverter:
    """Converts non-native document formats to a working-directory copy for processing.

    Native formats (pdf, png, jpg, jpeg, txt, md) are copied as-is.
    Convertible formats (docx, doc, odt, xlsx, …) are converted to PDF via
    LibreOffice headless.  The original source file is never modified.

    Lifecycle: ``boot()`` is called automatically in ``__init__``.  Call
    ``cleanup()`` when processing is complete to remove the working directory.
    """

    def __init__(self, helper_config: HelperConfig, working_directory: str) -> None:
        """Initialise the converter and create the working directory.

        Args:
            helper_config: Shared configuration and logger provider.
            working_directory: Base path for temporary output files.  A
                UUID-named subdirectory is created inside it.

        Raises:
            RuntimeError: If LibreOffice (``soffice`` or ``libreoffice``) is
                not found in PATH, or if the working directory cannot be created.
        """
        self.logging: logging.Logger = helper_config.get_logger()
        self._libreoffice: str | None = self._find_libreoffice()
        self._helper_file = HelperFile()
        # add subfolder with 8 chars uuid to avoid collisions when multiple documents are processed concurrently
        self._working_directory = os.path.join(working_directory, uuid4().hex[:8])

        # libreoffice is required for this helper
        if not self._libreoffice:
            raise RuntimeError("DocumentConverter: LibreOffice (soffice) is not installed or not in PATH")
        self.boot()

    ##########################################
    ############### CORE #####################
    ##########################################

    def boot(self) -> None:
        """Create the working directory.  Called automatically in ``__init__``."""
        # create directory for converted files if it does not exist
        if not self._helper_file.create_folder(self._working_directory):
            raise RuntimeError(f"DocumentConverter: failed to create working directory '{self._working_directory}' for converted files")

    def cleanup(self) -> None:
        """Remove the working directory and all converted files inside it."""
        # delete the working dir
        if not self._helper_file.remove_folder(self._working_directory):
            self.logging.warning(f"DocumentConverter: failed to delete working directory '{self._working_directory}' for converted files")

    def is_booted(self) -> bool:
        """Return True if the working directory exists and LibreOffice is available."""
        return self._helper_file.folder_exists(self._working_directory) and self._libreoffice is not None

    def _find_libreoffice(self) -> str | None:
        """Return the path to the LibreOffice binary, or None if not installed."""
        for candidate in ("soffice", "libreoffice"):
            path = shutil.which(candidate)
            if path:
                return path
        return None

    ##########################################
    ############### GETTER ###################
    ##########################################

    def _get_supported_extensions(self) -> list[str]:
        """Return extensions that can be processed without conversion (e.g. ``pdf``, ``png``)."""
        return ["pdf", "png", "jpg", "jpeg", "txt", "md"]

    def _get_extensions_to_convert(self) -> list[str]:
        """Return extensions that must be converted to PDF via LibreOffice before processing."""
        return ["docx", "doc", "odt", "ott", "xlsx", "xls", "ods", "csv", "pptx", "ppt", "odp", "rtf"]

    ##########################################
    ############# CONVERTER ##################
    ##########################################

    def convert(self, source_path: str) -> str:
        """Convert or copy the source file into the working directory.

        - Native formats are copied to a UUID-named file in the working directory.
        - Convertible formats are converted to PDF via LibreOffice and the
          resulting PDF is placed in the working directory.
        - Unsupported extensions raise ``RuntimeError`` immediately.

        Args:
            source_path: Absolute path to the source document.

        Returns:
            Absolute path to the working-directory copy (native format) or
            the converted PDF.

        Raises:
            RuntimeError: If the converter is not booted, the file extension is
                unsupported, the copy fails, or LibreOffice conversion fails.
        """
        # check if helper is booted
        if not self.is_booted():
            raise RuntimeError("DocumentConverter: cannot convert because helper is not booted")

        # if the given path has already a native extension, copy it to a temp file and return the new path
        if self._helper_file.get_file_extension(source_path, True, True) in self._get_supported_extensions():
            path = os.path.join(self._working_directory, f"{uuid4().hex[:8]}.{self._helper_file.get_file_extension(source_path, True, True)}")
            if self._helper_file.copy_file(source_path, path) is None:
                raise RuntimeError(f"DocumentConverter: failed to copy file '{source_path}' to '{path}'")
            return path

        # if file is not even in a convertible format, raise an error
        if self._helper_file.get_file_extension(source_path, True, True) not in self._get_extensions_to_convert():
            raise RuntimeError(f"DocumentConverter: unsupported file extension '{self._helper_file.get_file_extension(source_path, True, True)}' for file '{source_path}'")

        # convert the file to PDF and return the new path
        path = os.path.join(self._working_directory, f"{uuid4().hex[:8]}.pdf")
        self._convert_to_pdf(source_file=source_path, target_file=path)
        return path

    async def _convert_to_pdf(self, source_file: str, target_file: str) -> None:
        """Run LibreOffice headless to convert *source_file* to PDF in *target_file*.

        Args:
            source_file: Source document path.
            target_file:   Temporary output file path.

        Raises:
            RuntimeError: If LibreOffice is unavailable or conversion fails.
            FileNotFoundError: If the expected PDF output was not created.
        """
        # check if helper is booted
        if not self.is_booted():
            raise RuntimeError("DocumentConverter: cannot convert because helper is not booted")

        self.logging.debug(
            "DocumentConverter: converting '%s' to PDF via LibreOffice...", source_file
        )
        tmp_dir = os.path.join(self._working_directory, f"libreoffice_tmp_{uuid4().hex[:8]}")
        if not self._helper_file.create_folder(tmp_dir):
            raise RuntimeError(f"DocumentConverter: failed to create temporary directory '{tmp_dir}' for conversion")

        proc = await asyncio.create_subprocess_exec(
            self._libreoffice,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", tmp_dir,
            source_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                "LibreOffice exited with code %d: %s"
                % (proc.returncode, stderr.decode(errors="replace").strip())
            )

        # LibreOffice names the output <basename>.pdf in out_dir
        base_name = self._helper_file.get_basename(source_file)
        pdf_path = os.path.join(tmp_dir, f"{base_name}.pdf")

        # if file not found, raise an error with the LibreOffice output for debugging
        if not self._helper_file.file_exist(pdf_path):
            raise FileNotFoundError(
                "Expected PDF output not found at '%s'. "
                "LibreOffice stdout: %s, stderr: %s"
                % (pdf_path, stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip())
            )

        # move the generated PDF to the target path and delete the temp dir
        if not self._helper_file.move_file(pdf_path, target_file):
            raise RuntimeError(f"DocumentConverter: failed to move converted PDF from '{pdf_path}' to '{target_file}'")
