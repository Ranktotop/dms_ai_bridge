from services.doc_ingestion.Exceptions import PathTemplateValidationError, DocumentPathValidationError
from collections.abc import Callable
from services.doc_ingestion.Dataclasses import DocMetadata
from shared.helper.HelperConfig import HelperConfig
from shared.helper.HelperFile import HelperFile
import os
import re
from dataclasses import dataclass
from typing import Literal


@dataclass
class TemplateSegment:
    """Represents a single segment in the path template with its zone assignment."""
    index:int
    fieldname: str
    variable_name: str
    is_static: bool
    is_placeholder: bool
    is_last: bool = False
    validator: Callable[[str], bool] | None = None


class PathTemplateParser:
    """Helper class for validating a path template and parsing file paths into DocMetadata.

    Uses a bidirectional zone approach to handle elastic middle segments:

        LEFT_ZONE    — strict-required fields (year/month/day) and adjacent literals,
                       consumed from the left in fixed positions
        ELASTIC_ZONE — named <optional> fields; unmatched middle segments become tags
        RIGHT_ZONE   — loose-required fields (correspondent/document_type/title) and
                       adjacent literals, consumed from the right in fixed positions
        FILENAME     — always the last segment, always required

    This design allows arbitrary extra path segments between the LEFT and RIGHT anchors;
    those extras accumulate as tags on the returned DocMetadata.
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, path_template: str, helper_config: HelperConfig) -> None:
        self.logging = helper_config.get_logger()
        self._raw_path_template = path_template
        self._helper_config = helper_config
        self._helper_file = HelperFile()
        self._template_segments: list[TemplateSegment] = []
        self._load_path_template()

    def _load_path_template(self) -> None:
        """Parse the raw path template and populate the three zone segment lists.

        Template syntax:
            <field>   — Required field. Must be present in the supported fields list and will be validated by its callback.
            literal   — bare string without brackets: a static folder name
            [filename] — always required and must be the last segment

        Raises:
            PathTemplateValidationError: If the template is invalid (empty, wrong ending,
                unsupported field name, or duplicate field name).
        """
        if not self._raw_path_template or not self._raw_path_template.endswith("<filename>"):
            raise PathTemplateValidationError(
                "Invalid path template '%s': must be non-empty and end with <filename>"
                % self._raw_path_template
            )

        template_str = self._raw_path_template.lstrip("/").rstrip("/")
        segments = template_str.split("/")
        supported_fields = self._get_supported_fields()

        #iterate all segments
        template_segments:list[TemplateSegment] = []
        for index, segment in enumerate(segments):
            #check if wrapped in <>
            is_placeholder = bool(re.match(r"^[\[<][^/]+[\]>]$", segment))
            fieldname = segment if not is_placeholder else segment[1:-1].strip().lower()
            #if is placeholder, make sure its in supported fields
            if is_placeholder and fieldname not in supported_fields:
                raise PathTemplateValidationError(
                    "Invalid path template '%s': field name '%s' in segment '%s' is not supported. Supported fields are: %s"
                    % (self._raw_path_template, fieldname, segment, ", ".join(sorted(supported_fields)))
                ) 
            #if placeholder, make sure its unique
            if is_placeholder and any(s.fieldname == fieldname for s in template_segments):
                raise PathTemplateValidationError(
                    "Invalid path template '%s': duplicate field name '%s' in segment '%s'. Each field name can only be used once."
                    % (self._raw_path_template, fieldname, segment)
                )
            #if placeholder is filename, mark as last
            is_last = is_placeholder and fieldname == "filename"
            template_segments.append(TemplateSegment(
                index=index,
                fieldname=segment,
                variable_name=fieldname,
                is_static=not is_placeholder,
                is_placeholder=is_placeholder,
                is_last=is_last,
                validator=supported_fields.get(fieldname) if is_placeholder else None,
            ))
        self._template_segments = template_segments

    ##########################################
    ############## GETTER ####################
    ##########################################

    def _get_supported_fields(self) -> dict[str, Callable[[str], bool]]:
        """Return supported field names mapped to their validator callbacks."""
        return {
            "correspondent": self._validate_non_empty,
            "document_type": self._validate_non_empty,
            "year":          self._validate_year,
            "month":         self._validate_month,
            "day":           self._validate_day,
            "title":         self._validate_non_empty,
            "filename":      self._validate_non_empty,
        }

    ##########################################
    ############### CORE #####################
    ##########################################

    def get_segments_for_path(self, file_path: str, root_path: str, reverse_order: bool = False) -> list[str]:
        """
        Get the segments of the relative path from root_path to file_path. 
        If reverse_order is True, return the segments in reverse order (useful for right zone processing).

        Args:
            file_path: The full file path to be parsed.
            root_path: The root path to which the file path is relative.
            reverse_order: Whether to return the segments in reverse order.

        Returns:
            A list of path segments in the specified order.        
        """
        try:
            file_path_rel = os.path.relpath(file_path, root_path)
        except ValueError:
            file_path_rel = os.path.basename(file_path)
        file_path_rel = file_path_rel.replace("\\", os.sep) #use system separator
        segments = file_path_rel.split(os.sep)
        return segments[::-1] if reverse_order else segments

    def _strip_until_static_match(self, file_path: str, root_path: str)->str:
        """
        Strip segments from the file path until a static segment from the template is matched. 
        This is used to find the correct starting point for parsing when there are extra segments in the path.

        Args:
            file_path: The full file path to be parsed.
            root_path: The root path to which the file path is relative.

        Returns:
            The stripped file path starting from the first matched static segment.        
        """
        segments = self.get_segments_for_path(file_path, root_path)

        # find position of first static segment in template
        static_segment_index = next((i for i, s in enumerate(self._template_segments) if s.is_static), None)
        if static_segment_index is None:
            return file_path  # no static segments, return original path
        static_segment = self._template_segments[static_segment_index]

        # find position of first matching static segment in path
        matching_segment_index = None
        for i, segment in enumerate(segments):
            if segment.lower() == static_segment.fieldname.lower():
                matching_segment_index = i
                break

        # if no matching static segment is found, raise error
        if matching_segment_index is None:
            raise DocumentPathValidationError(
                "Static segment '%s' not found in path '%s'"
                % (static_segment.fieldname, file_path)
            )

        #if the index matches the path is already correctly aligned with the template, so we can return it as is
        if matching_segment_index == static_segment_index:
            return file_path

        #if the matching index is lower than the static index, we do not have enough segments to the left of the static segment to satisfy the template, so we can already fail
        if matching_segment_index < static_segment_index:
            raise DocumentPathValidationError(
                "Path '%s' does not have enough segments before static segment '%s' to satisfy template '%s'"
                % (file_path, static_segment.fieldname, self._raw_path_template)
            )        

        # since we have more segments before the matching part, we can strip the path to start from the matching static segment
        # E.g. 
        # Template: "<year>/documents/<correspondent>/def/<document_type>/<filename>" -> Index 1
        # Path is "extra/segments/2026/documents/correspondent/def/document_type/filename" -> Matching index is 3
        # Since we know the template requires at least 1 segment before the static "documents", we can ignore all segments from 0 to matching index - static index
        segments_to_strip = matching_segment_index - static_segment_index
        stripped_segments = segments[segments_to_strip:]
        return os.path.join(root_path, *stripped_segments)


    def convert_path_to_metadata(self, file_path: str, root_path: str) -> DocMetadata:
        """
        Parse a file path into DocMetadata using bidirectional template matching.

        Rules:
        - Left-to-right: assign template segments until mismatch
        - Right-to-left: assign template segments until mismatch
        - Anything in between -> elastic part -> meta.tags
        - All template placeholders must be filled, otherwise raise DocumentPathValidationError
        """

        # --------------------------
        # 1. Relative path segments
        # --------------------------
        stripped_path = self._strip_until_static_match(file_path, root_path)
        path_segments = self.get_segments_for_path(stripped_path, root_path)
        if not path_segments:
            raise DocumentPathValidationError(f"Path '{file_path}' contains no segments")

        meta = DocMetadata()
        template_segments = self._template_segments
        template_no_filename = template_segments[:-1]  # all segments except <filename>

        # --------------------------
        # 2. Filename (always last)
        # --------------------------
        filename_segment = path_segments.pop()
        if not self._helper_file.get_file_extension(filename_segment):
            raise DocumentPathValidationError(f"Last segment '{filename_segment}' is not a valid filename")
        meta.filename = filename_segment

        # --------------------------
        # 3. Initialize pointers for bidirectional matching
        # --------------------------
        left_template_idx = 0
        right_template_idx = len(template_no_filename) - 1
        left_path_idx = 0
        right_path_idx = len(path_segments) - 1

        # Store assigned placeholder values
        assigned_placeholders = {}

        # --------------------------
        # 4. Left-to-right matching
        # --------------------------
        while left_template_idx <= right_template_idx and left_path_idx <= right_path_idx:
            tmpl = template_no_filename[left_template_idx]
            seg = path_segments[left_path_idx]

            if tmpl.is_static:
                if seg.lower() != tmpl.fieldname.lower():
                    break  # stop left matching
            elif tmpl.is_placeholder:
                # validate segment if validator exists
                if tmpl.validator and not tmpl.validator(seg):
                    break
                assigned_placeholders[tmpl.variable_name] = seg
            left_template_idx += 1
            left_path_idx += 1

        # --------------------------
        # 5. Right-to-left matching
        # --------------------------
        while right_template_idx >= left_template_idx and right_path_idx >= left_path_idx:
            tmpl = template_no_filename[right_template_idx]
            seg = path_segments[right_path_idx]

            if tmpl.is_static:
                if seg.lower() != tmpl.fieldname.lower():
                    break  # stop right matching
            elif tmpl.is_placeholder:
                if tmpl.validator and not tmpl.validator(seg):
                    break
                assigned_placeholders[tmpl.variable_name] = seg
            right_template_idx -= 1
            right_path_idx -= 1

        # --------------------------
        # 6. Middle segments -> tags
        # --------------------------
        if left_path_idx <= right_path_idx:
            meta.tags = path_segments[left_path_idx:right_path_idx + 1]
        else:
            meta.tags = []

        # --------------------------
        # 7. Assign placeholders and enforce all filled
        # --------------------------
        for tmpl in template_no_filename:
            if tmpl.is_placeholder:
                value = assigned_placeholders.get(tmpl.variable_name)
                if not value:
                    raise DocumentPathValidationError(
                        f"Required field '{tmpl.variable_name}' in template '{self._raw_path_template}' "
                        f"was not found in path '{file_path}'"
                    )
                setattr(meta, tmpl.variable_name, value)

        return meta
    

    ##########################################
    ############# VALIDATORS #################
    ##########################################

    def _validate_year(self, value: str) -> bool:
        """Return True if value is a 4-digit year string (e.g. '2023')."""
        return bool(re.match(r"^\d{4}$", value))

    def _validate_month(self, value: str) -> bool:
        """Return True if value is a 1-or-2-digit month string (e.g. '01', '12')."""
        return bool(re.match(r"^\d{1,2}$", value))

    def _validate_day(self, value: str) -> bool:
        """Return True if value is a 1-or-2-digit day string (e.g. '1', '31')."""
        return bool(re.match(r"^\d{1,2}$", value))

    def _validate_non_empty(self, value: str) -> bool:
        """Return True if value is a non-empty, non-whitespace string."""
        return bool(value.strip())
