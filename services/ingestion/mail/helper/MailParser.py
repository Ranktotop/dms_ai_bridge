"""Parser for raw MIME email bytes into body and attachment inputs."""
import email
import email.header
import email.utils
import os
from dataclasses import dataclass
from html.parser import HTMLParser

import re

import markdownify
from bs4 import BeautifulSoup

from shared.helper.HelperConfig import HelperConfig


@dataclass
class MailBodyInput:
    """Plain-text body extracted from an email, ready for document ingestion."""
    content: str                        # plain text (HTML stripped if necessary)
    html_content: str | None = None     # raw HTML body when present alongside or instead of plain text
    markdown_content: str | None = None # html_content converted to Markdown; None when no HTML present

    def is_valid_markdown(self) -> bool:
        """
        Return True when markdown_content is present and passes quality gates.

        Gate 1 — no HTML leakage: surviving HTML tags indicate markdownify
        failed to fully process the input and the result is broken markup.

        Gate 2 — length ratio: markdown shorter than 30% of the plain-text
        baseline means the conversion silently dropped most of the content.

        Returns:
            bool: True if markdown_content is usable, False otherwise.
        """
        if not self.markdown_content:
            # no HTML part was present — nothing to validate
            return False

        # HTML leakage gate: surviving tags signal a broken conversion
        if re.search(r"<[a-zA-Z][^>]*>", self.markdown_content):
            return False

        # length ratio gate: markdown shorter than 30% of the plain-text
        # baseline indicates content was silently dropped during conversion
        plain_len = len(self.content)
        if plain_len > 0 and len(self.markdown_content) / plain_len < 0.3:
            return False

        return True


@dataclass
class MailAttachmentInput:
    """A single email attachment extracted from a MIME message."""
    file_bytes: bytes
    filename: str


@dataclass
class ParsedMail:
    """Structured representation of a parsed email, including body and attachments."""
    sender_name: str                    # display name from the From header (e.g. "Anthropic, PBC")
    sender_mail: str                    # address from the From header (e.g. "invoice@mail.anthropic.com")
    subject: str
    year:str
    month:str
    day:str
    hour:str
    minute:str
    second:str
    owner_id: int
    mail_content: MailBodyInput | None  # None when ingest_body=False or body is empty
    attachments: list[MailAttachmentInput]


class _HtmlStripper(HTMLParser):
    """Minimal HTML → plain-text converter using stdlib html.parser.

    Strips all tags and decodes HTML entities, preserving text content.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        # accumulate all visible text chunks — tags are discarded implicitly
        self._parts.append(data)

    def get_text(self) -> str:
        """Return the concatenated plain text."""
        return "".join(self._parts)


class MailParser:
    """Parses raw MIME email bytes into MailBodyInput and MailAttachmentInput objects.

    Uses Python's stdlib email module and markdownify for HTML → Markdown conversion.

    Usage:
        parser = MailParser(helper_config)
        body, attachments = parser.parse(raw_bytes, owner_id, attachment_extensions, ingest_body)
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig) -> None:
        self.logging = helper_config.get_logger()

    ##########################################
    ############### CORE #####################
    ##########################################

    def parse(
        self,
        raw_bytes: bytes,
        owner_id: int,
        attachment_extensions: frozenset[str],
        ingest_body: bool,
    ) -> ParsedMail:
        """Parse raw email bytes into a structured ParsedMail object.

        Args:
            raw_bytes: The complete raw RFC 2822 email as bytes.
            owner_id: DMS owner_id for the parsed mail.
            attachment_extensions: Set of allowed attachment file extensions
                (lowercase, without leading dot).
            ingest_body: If False the body is not extracted and mail_content
                is set to None on the returned ParsedMail.

        Returns:
            ParsedMail: Structured representation with metadata, body, and attachments.
        """
        msg = email.message_from_bytes(raw_bytes)

        subject = self._decode_header_value(msg.get("Subject", ""))
        sender_name, sender_mail = self._parse_sender(msg.get("From", ""))
        date_str = msg.get("Date", "")

        body_text: str | None = None
        raw_html: str | None = None      # preserved separately so callers can use the original markup
        attachments: list[MailAttachmentInput] = []

        # walk all MIME parts recursively to collect body text and attachments
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in msg.walk():
                content_disposition = part.get_content_disposition() or ""
                content_type = part.get_content_type()

                # skip multipart containers — we only process leaf parts
                if part.get_content_maintype() == "multipart":
                    continue

                # attachments have a content-disposition of "attachment"
                if "attachment" in content_disposition:
                    attachment = self._extract_attachment(part, attachment_extensions)
                    if attachment is not None:
                        attachments.append(attachment)
                    continue

                # collect body parts by content type
                if content_type == "text/plain":
                    plain_parts.append(self._decode_part_payload(part))
                elif content_type == "text/html":
                    html_parts.append(self._decode_part_payload(part))

            # always preserve raw HTML when present — even if plain text wins as content
            if html_parts:
                raw_html = "\n\n".join(h for h in html_parts if h.strip()) or None

            # prefer plain text; fall back to HTML stripped of tags
            if plain_parts:
                body_text = "\n\n".join(p for p in plain_parts if p.strip())
            elif raw_html:
                body_text = self._strip_html(raw_html)
        else:
            # single-part message
            content_type = msg.get_content_type()
            if content_type == "text/plain":
                body_text = self._decode_part_payload(msg)
            elif content_type == "text/html":
                raw_html = self._decode_part_payload(msg) or None
                body_text = self._strip_html(raw_html) if raw_html else None

        mail_content: MailBodyInput | None = None
        if ingest_body and body_text and body_text.strip():
            mail_content = MailBodyInput(
                content=body_text.strip(),
                html_content=raw_html,
                # convert HTML to Markdown only when raw HTML is available — plain-text-only
                # mails don't need it and markdownify would just echo the text unchanged
                markdown_content=self._html_to_markdown(raw_html) if raw_html else None,
            )
        date_dict = self._parse_date(date_str)
        return ParsedMail(
            sender_name=sender_name,
            sender_mail=sender_mail,
            subject=subject,
            year=date_dict["year"],
            month=date_dict["month"],
            day=date_dict["day"],
            hour=date_dict["hour"],
            minute=date_dict["minute"],
            second=date_dict["second"],
            owner_id=owner_id,
            mail_content=mail_content,
            attachments=attachments,
        )

    ##########################################
    ############# HELPERS ####################
    ##########################################

    def _parse_date(self, date_str: str) -> dict:
        """
        Parses a mail date in format 'Tue, 3 Mar 2026 12:07:53 +0000' and returns a dict with keys 'day', 'month', 'year', 'hour', 'minute', 'second'.
        """
        parsed_tuple = email.utils.parsedate_tz(date_str)
        if not parsed_tuple:
            raise ValueError(f"Unable to parse date string: {date_str}")
        return {
            "day": parsed_tuple[2],
            "month": parsed_tuple[1],
            "year": parsed_tuple[0],
            "hour": parsed_tuple[3],
            "minute": parsed_tuple[4],
            "second": parsed_tuple[5],
        }


    def _parse_sender(self, raw_from: str) -> tuple[str, str]:
        """Split a raw From header value into display name and email address.

        Uses stdlib email.utils.parseaddr which handles RFC 2822 address formats
        including quoted display names with commas (e.g. '"Anthropic, PBC" <x@y.com>').

        Args:
            raw_from: Raw value of the From header, possibly RFC 2047-encoded.

        Returns:
            Tuple of (display_name, email_address). Either may be an empty string
            when the header is absent or contains only an address without a name.
        """
        decoded = self._decode_header_value(raw_from)
        name, addr = email.utils.parseaddr(decoded)
        return name, addr

    def _decode_header_value(self, raw_value: str) -> str:
        """Decode an RFC 2047-encoded email header value to a plain string.

        Handles multi-segment encoded-word headers and mixed ASCII/encoded parts.
        Returns an empty string if the header is missing or empty.
        """
        if not raw_value:
            return ""
        parts: list[str] = []
        for fragment, charset in email.header.decode_header(raw_value):
            if isinstance(fragment, bytes):
                # decode with the declared charset, falling back to latin-1 if unknown
                parts.append(fragment.decode(charset or "latin-1", errors="replace"))
            else:
                parts.append(fragment)
        return "".join(parts)

    def _decode_part_payload(self, part: email.message.Message) -> str:
        """Decode the payload of a MIME part to a plain string.

        Respects the part's declared charset; falls back to utf-8 with error
        replacement if the charset is absent or unrecognised.
        """
        payload = part.get_payload(decode=True)
        if not payload:
            return ""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            # charset name unknown to Python — fall back to utf-8
            return payload.decode("utf-8", errors="replace")

    def _strip_html(self, html: str) -> str:
        """Strip HTML tags from *html* and return the visible plain text."""
        stripper = _HtmlStripper()
        stripper.feed(html)
        return stripper.get_text()

    def _clean_markdown_whitespace(self, text: str) -> str:
        """Remove invisible Unicode spacers and collapse excessive blank lines.

        HTML emails use several invisible characters as preheader padding (e.g.
        U+034F COMBINING GRAPHEME JOINER, U+00AD SOFT HYPHEN) and &nbsp; entities
        that become U+00A0 NON-BREAKING SPACE. These make lines appear non-empty
        to a simple newline-count regex, so we strip them first before collapsing.

        Args:
            text: Raw Markdown string from markdownify.

        Returns:
            Cleaned Markdown with at most one blank line between blocks.
        """
        # remove invisible email spacer characters used for preheader padding
        text = re.sub(r"[\u034f\u00ad\u200b\u200c\u200d\ufeff]", "", text)
        # replace non-breaking spaces with regular spaces so whitespace-only lines collapse
        text = text.replace("\u00a0", " ")
        # blank out lines containing only whitespace (tabs, spaces, nbsp remnants)
        # [^\S\n] matches any whitespace character except newline
        text = re.sub(r"^[^\S\n]+$", "", text, flags=re.MULTILINE)
        # collapse 3+ consecutive newlines to a single blank line
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _preprocess_html_for_markdown(self, html: str) -> str:
        """Pre-process HTML before Markdown conversion.

        Removes non-content blocks (style, script, head) and unwraps layout tables.
        The reliable heuristic: real data tables always have 'th' header cells;
        HTML email layout tables never do. Layout tables are unwrapped so their
        cell content flows into the surrounding document as plain blocks — the table
        grid structure disappears but no text is lost. Data tables (with 'th') are
        left intact for markdownify to convert to pipe tables.

        Tables are processed in reverse document order (innermost first) so that
        nested layout tables are resolved before their parent is unwrapped.

        Args:
            html: Raw HTML string.

        Returns:
            Pre-processed HTML string ready for markdownify.
        """
        soup = BeautifulSoup(html, "html.parser")

        # remove metadata, CSS blocks, and images entirely — they contain no readable content
        for tag in soup.find_all(["style", "script", "head", "img"]):
            tag.decompose()

        # remove elements hidden via inline style — email preheaders use display:none
        # to inject invisible spacer text that must never appear in the output
        for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.IGNORECASE)):
            tag.decompose()

        # unwrap layout tables from innermost to outermost — reversed() gives bottom-up order
        for table in reversed(soup.find_all("table")):
            if table.find("th"):
                # data table with semantic headers — keep for pipe-table conversion
                continue
            # strip all table-structure tags but leave their children in place so
            # the cell content remains in the document at the correct position
            for structural in table.find_all(["tbody", "thead", "tfoot", "tr", "td"]):
                structural.unwrap()
            table.unwrap()

        return str(soup)

    def _html_to_markdown(self, html: str) -> str | None:
        """Convert an HTML string to Markdown using markdownify.

        HTML is pre-processed to remove layout tables before conversion — see
        _preprocess_html_for_markdown() for the full rationale. BeautifulSoup
        (used internally by markdownify) repairs malformed HTML, which matters
        given the inconsistent output of real-world email clients.

        Args:
            html: Raw HTML string to convert.

        Returns:
            Markdown string, or None if the result is empty after stripping.
        """
        preprocessed = self._preprocess_html_for_markdown(html)
        result = markdownify.markdownify(preprocessed, heading_style=markdownify.ATX)
        if not result:
            return None
        result = self._clean_markdown_whitespace(result)
        # return None rather than an empty string so callers can use a simple truthiness check
        return result or None

    def _extract_attachment(
        self,
        part: email.message.Message,
        attachment_extensions: frozenset[str],
    ) -> MailAttachmentInput | None:
        """Extract one MIME attachment part if its extension is in the allowed set.

        Args:
            part: The MIME part representing the attachment.
            attachment_extensions: Allowed lowercase extensions without leading dot.

        Returns:
            MailAttachmentInput if the attachment is allowed, otherwise None.
        """
        raw_filename = part.get_filename() or ""
        filename = self._decode_header_value(raw_filename)
        if not filename:
            # no filename — skip this attachment
            return None

        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        if ext not in attachment_extensions:
            # extension not in the allowed list — skip
            self.logging.debug(
                "Skipping attachment '%s': extension '.%s' not in allowed set.", filename, ext
            )
            return None

        file_bytes = part.get_payload(decode=True)
        if not file_bytes:
            self.logging.warning("Attachment '%s' has empty payload, skipping.", filename)
            return None

        return MailAttachmentInput(
            file_bytes=file_bytes,
            filename=filename,
        )
