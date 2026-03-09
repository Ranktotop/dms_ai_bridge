from services.doc_ingestion.Exceptions import PathTemplateValidationError, DocumentPathValidationError
from collections.abc import Callable
from services.doc_ingestion.Dataclasses import DocMetadata
from shared.helper.HelperConfig import HelperConfig
from shared.helper.HelperFile import HelperFile
import os
import re
from dataclasses import dataclass


@dataclass
class TemplateSegment:
    """Represents a single segment in the path template with its zone assignment."""
    index: int
    fieldname: str
    variable_name: str
    is_static: bool
    is_placeholder: bool
    is_wildcard: bool
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
        template_str = self._raw_path_template.lstrip("/").rstrip("/")
        segments = template_str.split("/")
        supported_fields = self._get_supported_fields()

        # iterate all segments
        template_segments: list[TemplateSegment] = []
        for index, segment in enumerate(segments):
            # check if wrapped in <> or [] to identify placeholders, and extract field name
            is_placeholder = bool(re.match(r"^[\[<][^/]+[\]>]$", segment))
            fieldname = segment if not is_placeholder else segment[1:-1].strip().lower()

            # if placeholder...
            if is_placeholder:
                # make sure its in supported fields
                if fieldname not in supported_fields:
                    raise PathTemplateValidationError(
                        "Invalid path template '%s': field name '%s' in segment '%s' is not supported. Supported fields are: %s"
                        % (self._raw_path_template, fieldname, segment, ", ".join(sorted(supported_fields)))
                    )

                # make sure its unique
                if any(s.fieldname.lower() == fieldname.lower() for s in template_segments):
                    raise PathTemplateValidationError(
                        "Invalid path template '%s': duplicate field name '%s' in segment '%s'. Each field name can only be used once."
                        % (self._raw_path_template, fieldname, segment)
                    )

                # if wildcard placeholder, mark as such
                is_wildcard = segment.startswith("[") and segment.endswith("]")

            template_segments.append(TemplateSegment(
                index=index,
                fieldname=segment,
                variable_name=fieldname,
                is_static=not is_placeholder and not is_wildcard,
                is_placeholder=is_placeholder,
                is_wildcard=is_wildcard,
                validator=supported_fields.get(fieldname) if is_placeholder else None,
            ))
        # make sure there is only one wildcard template
        if sum(s.is_wildcard for s in template_segments) > 1:
            raise PathTemplateValidationError(
                "Invalid path template '%s': multiple wildcard fields are not allowed. Found: %s"
                % (self._raw_path_template, ", ".join(s.fieldname for s in template_segments if s.is_wildcard))
            )
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
            "tags":          None
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
        file_path_rel = file_path_rel.replace("\\", os.sep)  # use system separator
        segments = file_path_rel.split(os.sep)
        return segments[::-1] if reverse_order else segments

    def _strip_until_static_match(self, file_path: str, root_path: str) -> str:
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

        # if the index matches the path is already correctly aligned with the template, so we can return it as is
        if matching_segment_index == static_segment_index:
            return file_path

        # if the matching index is lower than the static index, we do not have enough segments to the left of the static segment to satisfy the template, so we can already fail
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

    def _find_filename_in_segments(self, segments: list[str]) -> str | None:
        """
        Checks the last segment of given segments for a valid filename (must have an extension).

        Args:
            segments (list[str]): List of path segments to check.

        Returns:
            str | None: The filename if a valid one is found, otherwise None.
        """
        if not segments:
            return None
        if len(segments) == 1:
            filename_segment = segments[0]
        else:
            filename_segment = segments[-1]  # the last segment must be the filename
        if not self._helper_file.get_file_extension(filename_segment):
            return None
        return filename_segment

    def _fill_template(self, path_segments: list[str], path_templates: list[TemplateSegment], from_right: bool) -> DocMetadata:
        meta = DocMetadata()

        # if either segments or templates are empty, we cannot fill anything, return empty meta
        if not path_segments or not path_templates:
            return meta

        # make sure there is no wildcard template in given templates
        if any(t.is_wildcard for t in path_templates):
            raise DocumentPathValidationError(
                f"Internal error: _fill_template should not be called with templates containing wildcards. Given templates: {path_templates}"
            )

        # make sure filename is not included in the segments
        if self._helper_file.get_file_extension(path_segments[-1]):
            raise DocumentPathValidationError(
                f"Internal error: _fill_template should not be called with filename segment. Given segments: {path_segments}"
            )

        path = os.path.join(*path_segments)  # only for logging purposes

        # define max index to iterate based on the smaller of dynamic segments and template segments (excluding filename)
        max_index = min(len(path_segments), len(path_templates))
        # iterate from right to left
        for i in range(max_index):
            # get segment and template
            template = path_templates[-(i+1)] if from_right else path_templates[i]
            segment = path_segments[-(i+1)] if from_right else path_segments[i]

            # if static, it MUST match exactly (case-insensitive)
            if template.is_static:
                if segment.lower() != template.fieldname.lower():
                    raise DocumentPathValidationError(
                        f"Segment '{segment}' at position {len(path_segments)-1 - i} does not match expected static segment '{template.fieldname}' in template '{self._raw_path_template}' for path '{path}/FILENAME'"
                    )
                continue

            if template.is_placeholder:
                if template.validator and not template.validator(segment):
                    raise DocumentPathValidationError(
                        f"Segment '{segment}' at position {len(path_segments)-1 - i} failed validation for field '{template.variable_name}' in template '{self._raw_path_template}' for path '{path}/FILENAME'"
                    )
                setattr(meta, template.variable_name, segment)

        # collect the unprocessed segments as tags in meta
        if max_index < len(path_segments):
            if from_right:
                meta.tags = path_segments[:-max_index]
            else:
                meta.tags = path_segments[max_index:]
        return meta

    def convert_path_to_metadata(self, file_path: str, root_path: str) -> DocMetadata:
        """
        Parse a file path into DocMetadata using bidirectional template matching.

        Rules:
        - Left-to-right: assign template segments until mismatch
        - Right-to-left: assign template segments until mismatch
        - Anything in between -> elastic part -> meta.tags
        - All template placeholders must be filled, otherwise raise DocumentPathValidationError
        """

        # Make sure we start on the correct segment
        stripped_path = self._strip_until_static_match(file_path, root_path)
        path_segments = self.get_segments_for_path(stripped_path, root_path)
        if not path_segments:
            raise DocumentPathValidationError(f"Path '{file_path}' contains no segments")

        # if we have only one segment, it must be the filename
        filename = self._find_filename_in_segments(path_segments)
        if not filename:
            raise DocumentPathValidationError(f"Path '{file_path}' does not contain a valid filename segment")

        # Prepare meta
        meta = DocMetadata(filename=filename)
        # If we do only have a filename
        if len(path_segments) == 1:
            return meta

        # define all non filename segments and all non wildcard templates
        dynamic_segments = path_segments[:-1]
        dynamic_templates = [t for t in self._template_segments if not t.is_wildcard]

        # check if we have wildcard placeholders
        template_start_index = 0
        template_end_index = len(self._template_segments) - 1
        template_middle_index = template_end_index

        # search for wildcard template
        for template in self._template_segments:
            if template.is_wildcard:
                template_middle_index = template.index
                break

        # if template_middle_index is on index 0, fill from right only.
        if template_middle_index == 0:
            filled_meta = self._fill_template(
                path_segments=dynamic_segments,
                path_templates=dynamic_templates,
                from_right=True)
            filled_meta.filename = meta.filename  # preserve filename
            return filled_meta

        # if template_middle_index is on last index, fill from left only.
        elif template_middle_index == template_end_index:
            filled_meta = self._fill_template(
                path_segments=dynamic_segments,
                path_templates=dynamic_templates,
                from_right=False)
            filled_meta.filename = meta.filename  # preserve filename
            return filled_meta

        # slice templates into left and right based on the middle index
        left_templates = []
        left_segments = []
        right_templates = []
        right_segments = []
        # we use the bigger list as base for iteration
        max_index = max(len(dynamic_segments), len(dynamic_templates))
        for i in range(max_index):
            segment = dynamic_segments[i] if i < len(dynamic_segments) else None
            template = dynamic_templates[i] if i < len(dynamic_templates) else None
            # if we are on the left side...
            if i < template_middle_index:
                if template:
                    left_templates.append(template)
                if segment:
                    left_segments.append(segment)
            # if we are on the right side...
            else:
                if template:
                    right_templates.append(template)
                if segment:
                    right_segments.append(segment)
        # fill from left and right
        filled_left_meta = self._fill_template(
            path_segments=left_segments,
            path_templates=left_templates,
            from_right=False)
        filled_right_meta = self._fill_template(
            path_segments=right_segments,
            path_templates=right_templates,
            from_right=True)

        # now merge the filled metas. Since we splitted they should not have any overlapping fields
        for field in filled_left_meta.__dataclass_fields__:
            value = getattr(filled_left_meta, field)
            if value:
                setattr(meta, field, value)
        for field in filled_right_meta.__dataclass_fields__:
            value = getattr(filled_right_meta, field)
            if value:
                setattr(meta, field, value)
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
