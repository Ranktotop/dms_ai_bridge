import requests
import tarfile
import zipfile
import json
import tempfile
import re
from typing import Union, Optional
import shutil
import glob
import sys
import os
import hashlib
from pathlib import Path
from urllib.parse import urlparse
from shared.logging.logging_setup import setup_logging
logging = setup_logging()


class HelperFile:
    def get_files_recursive(self, path: str, extension: str = "", search: str = "", filter_list: list = [], asAbsolute: bool = False) -> list[str]:
        """
        Get all files in a folder and its subfolders recursively which match given rules.

        :param path: The root folder path
        :type path: str
        :param extension: The file extension
        :type extension: str
        :param search: If given, only files containing this substring are collected
        :type search: str
        :param filter_list: A blacklist. If given, only files not containing entries of the blacklist are collected
        :type filter_list: list        
        :param asAbsolute: Whether or not to return the paths as absolute paths
        :type asAbsolute: bool
        :return: A list of file paths for found files (relative or absolute)
        :rtype: list[str]
        """
        # check if path exists
        if not self.folder_exists(path):
            return []
        try:
            # Hole Dateien im aktuellen Verzeichnis mit `get_files`
            all_files = self.get_files(
                path, extension, search, filter_list, asAbsolute)

            # Durchlaufe alle Unterordner und rufe `get_files_recursive` auf
            for entry in os.listdir(path):
                full_path = os.path.join(path, entry)
                if os.path.isdir(full_path):  # Prüfe, ob es ein Ordner ist
                    all_files.extend(self.get_files_recursive(
                        full_path, extension, search, filter_list, asAbsolute))

            return all_files
        except Exception as e:
            logging.error(str(e))
            return []

    def get_subfolders(self, path: str, as_path: bool = True) -> list[str]:
        """
        Returns all subfolder names or paths in the given directory.

        Args:
            path (str): The path to the directory.
            as_path (bool, optional): If True, returns the full paths of the subfolders instead of their names. Defaults to True.

        Returns:
            list[str]: A list of subfolder names or paths.
        """
        path = self._convert_path(path, True)
        try:
            with os.scandir(path) as entries:
                if as_path:
                    folders = [entry.path for entry in entries if entry.is_dir()]
                else:
                    folders = [entry.name for entry in entries if entry.is_dir()]
            return folders
        except OSError:
            return []

    # get files in path and returns the relative paths as list
    def get_files(self, path: str, extension: str = "", search: str = "", filter_list: list = [], asAbsolute: bool = False) -> list[str]:
        """
        Get all files in folder which match given rules

        :param path: The path of folder
        :type path: str
        :param extension: The file extension without dot. E.g. "mp4"
        :type extension: str
        :param search: If given, only files containing this substring are collected
        :type search: str
        :param filter_list: A blacklist. If given, only files not containing entries of the blacklist are collected
        :type filter_list: list        
        :param asAbsolute: Whether or not to return the paths as absolute paths
        :type asAbsolute: bool
        :return: A list of relative file paths for found files
        :rtype: list[str]
        """
        try:
            path = self._convert_path(path, True)
            if not path or not self.folder_exists(path):
                return []
            extension = extension if extension else "*"
            pattern = f"*.{extension}"

            if search:
                pattern = f"*{search}*.{extension}"

            file_path_pattern = os.path.join(path, pattern)
            files = glob.glob(file_path_pattern)
            filtered_relatives = []

            # Filter filenames
            for f in files:
                valid = True
                for filter_string in filter_list:
                    if filter_string in f:
                        valid = False
                        break
                if valid:
                    finalPath = self.convert_absolute_path_to_relative(
                        f) if not asAbsolute else f
                    filtered_relatives.append(finalPath)
            return filtered_relatives
        except Exception as e:
            logging.error(str(e))
            return []

    # delete a file if exists
    def delete_file(self, path: str) -> bool:
        """
        Deletes a file

        :param path: The path of the file to delete
        :type path: str        
        :return: True on success, False otherwise
        :rtype: bool
        """
        # if file doesn't exists, take it as success
        if (not self.file_exist(path=path)):
            return True
        path = self._convert_path(path, True)
        try:
            os.remove(path)
            logging.debug(f"Successfully deleted file \"{path}\"!")
            return True
        except OSError as e:
            logging.error(f"Error deleting file \"{path}\"! -> "+str(e))
            return False

    def create_folder(self, path) -> bool:
        """
        Creates a folder if it doesn't exist already

        :param path: The relative path to the folder to create
        :type path: str        
        :return: True on success, False otherwise
        :rtype: bool
        """
        path = self._convert_path(path, True)

        # Check if folder already exists
        if os.path.isdir(path):
            return True

        try:
            # Create Folder
            os.makedirs(path)
            logging.debug(f"Successfully created folder \"{path}\"!")

            return True
        except Exception as e:
            logging.error(f"Error creating Folder \"{path}\"! -> " + str(e))
            return False

    def remove_folder(self, path: str) -> bool:
        """
        Deletes a folder if exists

        :param path: The path to the folder to delete
        :type path: str        
        :return: True on success, False otherwise
        :rtype: bool
        """
        path = self._convert_path(path, True)
        # check if folder exists
        if not (os.path.isdir(path)):
            logging.error(f"Can't delete folder {path} -> No directory!")
            return False
        try:
            shutil.rmtree(path)
            logging.debug(f"Successfully deleted folder \"{path}\"!")
            return True
        except Exception as e:
            logging.error(f"Error deleting folder \"{path}\"! -> "+str(e))
            return False

    def move_folder(self, source: str, destination: str) -> bool:
        src_path = self._convert_path(source, True)
        dst_path = self._convert_path(destination, True)

        # Überprüfe, ob das Quellverzeichnis existiert
        if not os.path.isdir(src_path):
            logging.error(f"Can't move folder {src_path} -> No directory!")
            return False

        # Überprüfe, ob das Zielverzeichnis existiert, und versuche es zu erstellen
        if not os.path.isdir(dst_path):
            if not self.create_folder(dst_path):
                logging.error(
                    f"Can't move folder to {dst_path} -> Error while creating folder!")
                return False

        # Durchlaufe alle Dateien und Unterverzeichnisse im Quellverzeichnis und verschiebe sie
        try:
            for item in os.listdir(src_path):
                src_item = os.path.join(src_path, item)
                dest_item = os.path.join(dst_path, item)
                shutil.move(src_item, dest_item)

            # Entferne das ursprüngliche Quellverzeichnis, wenn es leer ist
            os.rmdir(src_path)
            return True
        except Exception as e:
            logging.error(
                f"Failed to move folder contents from {src_path} to {dst_path}: {e}")
            return False

    def triangle_move_file(self, src_path: str, dst_path: str):
        # check if src exists
        if not self.file_exist(src_path):
            logging.error(
                f"Source file \"{src_path}\" does not exist. Can't triangle copy it!")
            return False

        # backup dst file to temp file if it exists
        backup_path = None
        if self.file_exist(dst_path):
            backup_path = self.generate_tempfile(
                self.get_file_extension(dst_path))
            if self.copy_file(dst_path, backup_path) is None:
                logging.error(
                    f"Error copying file \"{dst_path}\" to \"{backup_path}\". Can't safely triangle copy!")
                # delete temp file
                self.delete_file(backup_path)
                return False

            # delete current dst file
            if not self.delete_file(dst_path):
                logging.error(
                    f"Error deleting file \"{dst_path}\". Can't safely triangle copy!")
                # delete temp file
                self.delete_file(backup_path)
                return False

        # copy src to dst
        if not self.copy_file(src_path, dst_path):
            logging.error(
                f"Error copying file \"{src_path}\" to \"{dst_path}\". Can't safely triangle copy!")
            # restore dst file
            if backup_path is not None:
                self.copy_file(backup_path, dst_path)
                # delete backup file
                self.delete_file(backup_path)
            return False

        # delete src and backup file
        self.delete_file(src_path)
        if backup_path is not None:
            self.delete_file(backup_path)
        return True

    def convert_relative_path_to_absolute(self, relative: str, fromRoot: bool = True) -> str:
        """
        Converts a relative path to an absolute path

        :param relative: The relative path of file to get absolute path for
        :type relative: str
        :param fromRoot: Wether or not to retrieve path from root or callers folder
        :type fromRoot: bool
        :return: The absolute path to the given file
        :rtype: str
        """
        root_directory = os.environ['ROOT_DIR']  # without trailing slash. E.g. /home/user/app or C:\\users\ranktop\communit
        if not relative.startswith('/'):
            relative = "/"+relative

        # replace slashes and backslashes by system path separator
        relative = relative.replace("\\", os.sep)
        relative = relative.replace("//", os.sep)
        relative = relative.replace("/", os.sep)
        relative = relative.replace(
            os.sep+os.sep, os.sep)  # fix double slashes
        if (fromRoot):
            if getattr(sys, 'frozen', False):
                # Reguläres Ausdrucksmuster für den Pfadtrenner
                # Beachten Sie, dass der Backslash doppelt escaped werden muss, wenn er verwendet wird
                path_sep = re.escape(os.path.sep)

                pattern = f'(^|{path_sep})_internal({path_sep}|$)'
                root_directory = re.sub(pattern, lambda m: os.path.sep if m.group(
                    1) == os.path.sep and m.group(2) == os.path.sep else '', root_directory)
        relative = relative.lstrip(os.path.sep)  # remove leading slash
        return os.path.join(root_directory, relative)

    def convert_absolute_path_to_relative(self, absolute: str) -> str:
        """
        Converts an absolute path to a relative path

        :param absolute: The absolute path of file to get relative path for
        :type absolute: str
        :return: The relative path of the given file
        :rtype: str
        """
        # get root directory
        root_directory1 = os.environ['ROOT_DIR']

        # Reguläres Ausdrucksmuster für den Pfadtrenner
        # Beachten Sie, dass der Backslash doppelt escaped werden muss, wenn er verwendet wird
        path_sep = re.escape(os.path.sep)

        # remove internal on frozen exe
        pattern = f'(^|{path_sep})_internal({path_sep}|$)'
        root_directory2 = re.sub(pattern, lambda m: os.path.sep if m.group(
            1) == os.path.sep and m.group(2) == os.path.sep else '', root_directory1)
        thePath = absolute.replace(
            root_directory1+os.path.sep, '').replace(root_directory2+os.path.sep, '')
        return thePath

    # checks if a file exist
    def file_exist(self, path: str) -> bool:
        """
        Checks if the file on given path exists
        :param path: The file path to check existence for
        :type path: str
        :return: True if file exists, False otherwise
        :rtype: bool
        """
        path = self._convert_path(path, True)
        if (os.path.isfile(path)):
            return True
        return False

    def folder_exists(self, path: str) -> bool:
        """
        Überprüft, ob ein Ordner an dem angegebenen Pfad existiert.

        :param path: Pfad des zu prüfenden Ordners
        :type path: str
        :return: True, wenn der Ordner existiert, sonst False
        :rtype: bool
        """
        # Pfad anpassen, falls deine _convert_path-Methode dies erfordert
        path = self._convert_path(path, True)

        # Prüfen, ob es sich um einen Ordner handelt
        return os.path.isdir(path)

    def get_basename(self, filename: str, with_extension: bool = False) -> str:
        """
        Returns the base name of a file from the given filename.
        Examples:
            get_basename("myfile.mp4")          # "myfile"
            get_basename("archive.tar.gz")      # "archive"
            get_basename(".gitignore")          # ""
            get_basename("noextension")         # "noextension"
            get_basename("myfile.mp4", True)    # "myfile.mp4"
            get_basename("https://example.com/video.mp4")       # "video"
            get_basename("https://example.com/archive.tar.gz")  # "archive"
            get_basename("https://x.de/.gitignore")             # ""
            get_basename("https://x.de/download?id=123")        # "download"
        Args:
            filename (str): The path to the file.
            with_extension (bool, optional): If True, returns the base name with its extension.
                If False (default), returns the base name without its extension.
        Returns:
            str: The base name of the file, with or without extension based on the parameter.
        """
        # if the filename is a url, remove protocol and query params and try to get the basename if there is one
        parsed = urlparse(filename)
        if parsed.scheme and parsed.netloc:  # looks like a URL
            filename = parsed.path  # drop query/fragment/params

        basename_with_ext = os.path.basename(filename)
        if with_extension:
            return basename_with_ext

        extension = self.get_file_extension(basename_with_ext)
        if not extension:
            return basename_with_ext

        # Nur das letzte ".extension" entfernen
        return basename_with_ext[:-(len(extension) + 1)]

    def find_free_filename(self, basename: str, extension: str, folder: str) -> str:
        """
        Finds a free filename in the given folder by appending a number if necessary.

        Args:
            basename (str): The base name of the file without extension.
            extension (str): The file extension without dot.
            folder (str): The folder to check for existing files.

        Returns:
            str: A free filename with extension in the given folder.
        """
        # init values
        filename = f"{basename}.{extension}"
        folder_abs = self._convert_path(folder, True)
        target_path = os.path.join(folder_abs, filename)

        # if the basename + extension does not exist, return it
        if not self.file_exist(target_path):
            return filename

        # if it exists, append numerical suffix until a free filename is found
        suffix = 1
        filename = f"{basename}_{suffix:04d}.{extension}"
        target_path = os.path.join(folder_abs, filename)

        # check until a unique filename is found
        while self.file_exist(target_path):
            suffix += 1
            filename = f"{basename}_{suffix:04d}.{extension}"
            target_path = os.path.join(folder_abs, filename)
        return filename

    # write text to file
    def write_text_file(self, text, path) -> Union[str, None]:
        """
        Create text file with given content

        :param text: The content of destination file
        :type text: str
        :param path: The path of destination file
        :type path: str        
        :return: The relative output path on success, None otherwise
        :rtype: Union[str,None]
        """
        path = self._convert_path(path, True)
        try:
            file = open(path, "w", encoding='utf-8')
            file.write(text)
            file.close()
            return path
        except OSError as e:
            logging.error(f"Error writing text file \"{path}\"! -> "+str(e))
            return None

    def write_file_bytes(self, data: bytes, path: str) -> Union[str, None]:
        """
        Create binary file with given content

        :param data: The content of destination file
        :type data: bytes
        :param path: The path of destination file
        :type path: str
        :return: The relative output path on success, None otherwise
        :rtype: Union[str,None]
        """
        path = self._convert_path(path, True)
        try:
            with open(path, "wb") as file:
                file.write(data)
            return path
        except OSError as e:
            logging.error(f"Error writing binary file \"{path}\"! -> "+str(e))
            return None

    def write_json_file(self, data: dict, path: str) -> Union[str, None]:
        """
        Create text file with given content

        :param text: The content of destination file
        :type text: str
        :param path: The path of destination file
        :type path: str        
        :return: The output path on success, None otherwise
        :rtype: Union[str,None]
        """
        path = self._convert_path(path, True)
        try:
            # Validate that data can be serialized to JSON
            # This will raise an error if the data is not JSON serializable
            json.dumps(data)

            with open(path, 'w', encoding='utf-8') as json_file:
                # Optional: ensure_ascii=False for non-ASCII characters
                json.dump(data, json_file, indent=4, ensure_ascii=False)

            return path
        except (OSError, TypeError, ValueError) as e:
            logging.error(f"Error writing JSON file \"{path}\"! -> " + str(e))
            return None

    def is_text_file(self, path: str) -> bool:
        '''Checks if a file is a text file'''
        valids = ["txt", "md", "srt", "json"]
        path = self._convert_path(path, True)
        return self.get_file_extension(path) in valids

    def read_text_file(self, path) -> str | None:
        possible_encodings = ["utf-8", "iso-8859-1", "windows-1252", "latin1"]

        if not self.file_exist(path=path):
            logging.error(f"Die Datei '{path}' wurde nicht gefunden.")
            return None

        path = self._convert_path(path, True)

        for encoding in possible_encodings:
            try:
                with open(path, "r", encoding=encoding, errors="replace") as f:
                    return f.read()
            except UnicodeDecodeError:
                continue  # Falls es nicht klappt, probiere die nächste Kodierung

        # Falls keine der getesteten Kodierungen funktioniert, probiere erneut mit "ignore"
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            logging.warning(
                f"Datei '{path}' konnte nicht korrekt dekodiert werden. Kritische Zeichen wurden entfernt.")
            return content
        except Exception as e:
            logging.error(f"Fehlgeschlagen: {str(e)}")
            return None

    def read_json_file(self, path, asDict: bool = False) -> Union[dict, None]:
        if (not self.file_exist(path=path)):
            logging.debug(f"Die Datei '{path}' wurde nicht gefunden.")
            return None
        path = self._convert_path(path, True)
        try:
            json_content = self.read_text_file(path)
            if json_content is None:
                logging.debug(f"Fehler beim Lesen der Datei '{path}'.")
                return None
            data = json.loads(json_content)
            return data if asDict else json.dumps(data)
        except FileNotFoundError:
            logging.debug(f"Die Datei '{path}' wurde nicht gefunden.")
            return None
        except json.JSONDecodeError:
            logging.debug(
                f"Fehler beim Dekodieren von JSON in der Datei '{path}'.")
            return None
        except Exception as e:
            logging.debug(f"Ein Fehler ist aufgetreten: {str(e)}")
            return None

    # copy file
    def copy_file(self, path_in: str, path_out: str) -> Union[str, None]:
        """
        Copy the file from given input path to given output path
        If the target directory does not exists, it will be created
        If the target file already exists, it will be skipped if the checksum is the same

        Args:
            path_in (str): The path to file to copy
            path_out (str): The path of destination file

        Returns:
            Union[str, None]: The absolute output path on success, None otherwise
        """
        path_in = self._convert_path(path_in, True)
        path_out = self._convert_path(path_out, True)

        # if target directory does not exist, try to create it
        target_dir = self.get_folder_for_file(path_out)
        if not self.folder_exists(target_dir):
            if not self.create_folder(target_dir):
                logging.error(f"Error creating folder \"{target_dir}\" for file \"{path_out}\"! Can't copy!")
                return None

        # if the path out does already exists, compare checksums
        if self.file_exist(path_out):
            checksum_in = self.get_file_checksum(path_in)
            checksum_out = self.get_file_checksum(path_out)
            if checksum_in == checksum_out:
                logging.info(f"File \"{path_out}\" already exists with same checksum, skipping copy!")
                return path_out

        try:
            # copy srt
            shutil.copy2(path_in, path_out)
            return path_out
        except Exception as e:
            logging.error(
                f"Error copying file \"{path_in}\" to \"{path_out}\"! -> "+str(e))
            return None

    def move_file(self, path_in, path_out) -> bool:
        """
        Move the file from given input path to given output path

        :param path_in: The path to file to move
        :type path_in: str
        :param path_out: The path of destination file
        :type path_out: str

        :return: The relative output path on success, None otherwise
        :rtype: Union[str,None]
        """
        path_in = self._convert_path(path_in, True)
        path_out = self._convert_path(path_out, True)
        try:
            # move file
            shutil.move(path_in, path_out)
            return True
        except Exception as e:
            logging.error(
                f"Error moving file \"{path_in}\" to \"{path_out}\"! -> " + str(e))
            return False

    def save_audio_as_file(self, target_path: str, audio_stream: bytes) -> bool:
        """
        Writes an audio stream to file

        :param target_path: The path to write audio in
        :type target_path: str
        :param audio_stream: The audio-stream to write
        :type audio_stream: bytes
        :return: True if successful or False in error case
        :rtype: bool
        """
        path = self._convert_path(target_path, True)
        try:
            with open(path, "wb") as file:
                file.write(audio_stream)
            return True
        except Exception as e:
            logging.error(
                f"Error writing audio-stream to \"{path}\"! -> "+str(e))
            return False

    def sanitize_file_name(self, name: str, max_length: int = 255) -> str:
        """
        Removes invalid chars from name

        :param name: The name to sanitize
        :type name: str
        :param max_length: The max length for files/folders
        :type max_length: int
        :return: The sanitized file/folder name
        :rtype: str
        """
        # Liste der ungültigen Zeichen für Dateinamen unter Windows/Linux/macOS
        invalid_chars = r'[<>:"/\\|?*]'

        # Ersetze alle ungültigen Zeichen durch Unterstriche
        sanitized = re.sub(invalid_chars, '_', name)

        # Kürze den Dateinamen, falls er länger als max_length ist
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length]
        return sanitized

    def generate_tempfile(self, extension: str, asRelative: bool = False, path_only: bool = False, folder_path: Optional[str] = None) -> str:
        """
        Generates a unique temporary file. If folder path is given, the file will be created in that folder

        Args:
            extension (str): The extension of the file
            asRelative (bool, optional): Whether to return the relative path. Defaults to False.
            path_only (bool, optional): Whether to only return the path of the file. Defaults to False.
            folder_path (Optional[str], optional): The folder path to create the file in. Defaults to None.
        """
        # create temp folder if not existing
        root_dir = os.environ['ROOT_DIR']
        folder_path = os.path.join(root_dir, "temp") if not folder_path else self._convert_path(folder_path, True)

        if not self.create_folder(folder_path):
            raise Exception("Could not create temp folder at " + folder_path)

        if not extension.startswith("."):
            extension = "."+extension

        # Create tempfile and release it again
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=extension, dir=folder_path)
        temp_file.close()

        # get temp file path
        tempFilePath = temp_file.name if not asRelative else self.convert_absolute_path_to_relative(temp_file.name)

        # if path only delete the temp file
        if path_only:
            self.delete_file(tempFilePath)
        return tempFilePath

    def generate_tempfolder(self, asRelative: bool = False, path_only: bool = False, on_shared: bool = False) -> str:
        """
        Generates a unique temporary folder.

        :param asRelative: If True, returns the relative path instead of the absolute path
        :type asRelative: bool
        :param path_only: If True, returns the path only without creating the folder
        :type path_only: bool
        :param on_shared: If True, uses the shared temp folder
        :type on_shared: bool
        :return: The generated temp folder name as absolute path or relative if switch is on
        :rtype: str
        """
        # Root-Verzeichnis für temp erstellen
        root_directory = os.path.join(os.environ['ROOT_DIR'], "temp") if not on_shared else os.path.join(os.environ['ROOT_DIR'], "gateway_shared", "temp")
        self.create_folder(
            self.convert_absolute_path_to_relative(root_directory))

        # Erstelle einen einzigartigen temporären Ordner
        temp_folder = tempfile.mkdtemp(dir=root_directory)

        # if path only delete the temp folder
        if path_only:
            self.remove_folder(temp_folder)

        # Rückgabe des Pfades (relativ oder absolut)
        return temp_folder if not asRelative else self.convert_absolute_path_to_relative(temp_folder)

    def _convert_path(self, source: str, asAbsolute: bool) -> str:
        """
        Detects if the given path is relative or absolute and converts it

        :param source: The path to convert
        :type source: str        
        :param asAbsolute: Whether or not to convert to absolute path
        :type asAbsolute: bool           

        :return: The converted path
        :rtype: str
        """
        # if the source is absolute
        if (os.path.isabs(source)):
            if (asAbsolute):
                return source
            else:
                return self.convert_absolute_path_to_relative(source)
        # if the source is relative
        else:
            if (asAbsolute):
                return self.convert_relative_path_to_absolute(source)
            else:
                return source

    def get_path_type(self, path: str) -> str:
        """
        Checks if the given path is a file or directory.
        Works even if the path does not exist.

        Returns:
            str: "file", "directory", or "unknown"
        """
        p = Path(path)
        path_str = str(path)

        # 1. Existenz prüfen (falls vorhanden)
        if os.path.isdir(path):
            return "directory"
        if os.path.isfile(path):
            return "file"

        # 2. Slash/Backslash am Ende → Ordner
        if path_str.endswith(os.sep) or (os.altsep and path_str.endswith(os.altsep)):
            return "directory"

        # 3. Hat eine Dateiendung → Datei
        if p.suffix:
            return "file"

        # 4. Übergeordneter Ordner existiert und Pfad hat keine Extension → wahrscheinlich Ordner
        parent_dir = p.parent
        if parent_dir.exists() and not p.suffix:
            return "directory"

        # 4. Unklar → unknown oder Standard
        return "unknown"

    def is_directory(self, path: str) -> bool:
        """
        Checks if the given path is a directory.

        Args:
            path (str): The path to check

        Returns:
            bool: True if the path is a directory, False otherwise
        """
        return self.get_path_type(path) == "directory"

    def is_file(self, path: str) -> bool:
        """
        Checks if the given path is a file.

        Args:
            path (str): The path to check

        Returns:
            bool: True if the path is a file, False otherwise
        """
        return self.get_path_type(path) == "file"

    def save_audio_as_file(self, target_path: str, audio_stream: bytes) -> bool:
        """
        Writes an audio stream to file

        :param target_path_relative: The relative path to write audio in
        :type target_path_relative: str
        :param audio_stream: The audio-stream to write
        :type audio_stream: bytes
        :return: True if successful or False in error case
        :rtype: bool
        """
        path = self._convert_path(target_path, True)
        try:
            with open(path, "wb") as file:
                file.write(audio_stream)
            return True
        except Exception as e:
            logging.error(
                f"Error writing audio-stream to \"{path}\"! -> "+str(e))
            return False

    def get_file_extension(self, path: str, toLower: bool = False, force_single_dot:bool = False) -> str:
        """
        Returns the file extension of a given filename, without the leading dot.
        For files with multiple dots, everything after the first dot in the
        basename is considered the extension.

        Examples:
            get_file_extension("myfile.mp4")          # "mp4"
            get_file_extension("archive.tar.gz")      # "tar.gz"
            get_file_extension(".gitignore")          # "gitignore"
            get_file_extension("noextension")         # ""
            get_file_extension("https://example.com/video.mp4")       # "mp4"
            get_file_extension("https://example.com/archive.tar.gz")  # "tar.gz"
            get_file_extension("https://x.de/.gitignore")             # "gitignore"
            get_file_extension("https://x.de/download?id=123")        # ""

        Args:
            filename (str): The path to the file.
            toLower (bool): If True, returns the extension in lowercase.

        Returns:
            str: The file extension without the leading dot, or an empty string
            if no extension exists.
        """
        # Handle URLs: strip scheme, query, fragment
        parsed = urlparse(path)
        if parsed.scheme and parsed.netloc:  # looks like a URL
            path = parsed.path  # ignore query/fragment/params

        # if path starts with dot, add a temporary filename
        if path.startswith("."):
            path = "temp"+path
        path = self._convert_path(path, True)
        basename = os.path.basename(path)
        if "." not in basename:
            return ""
        # if force_single_dot, only return the part after the last dot
        if force_single_dot:
            ext = basename.rsplit(".", 1)[1]  # return all after last dot in basename
        else:
            ext = basename.split(".", 1)[1]  # return all after first dot in basename
        if toLower:
            ext = ext.lower()
        return ext

    def is_file_type(self, path: str, extension: str) -> bool:
        """
        Checks if the path ending is the given one
        :param path: Path to the file to check
        :type path: str
        :param extension: The extension to check for
        :type extension: str

        :return: True if extension, False otherwise
        :rtype: bool
        """
        return self.get_file_extension(path) == extension

    def get_file_size(self, file_path: str) -> float:
        """
        Gibt die Größe einer Datei in Megabyte (MB) zurück.

        :param file_path: Pfad zur Datei.
        :return: Dateigröße in MB als float.
        """
        file_path = self._convert_path(file_path, True)
        if not self.file_exist(file_path):
            return 0

        file_size_bytes = os.path.getsize(file_path)  # Größe in Bytes
        file_size_mb = file_size_bytes / (1024 * 1024)  # Umrechnung in MB
        return file_size_mb  # Auf 2 Nachkommastellen runden

    def extract_archive(self, archive_path: str, target_folder: str) -> bool:
        """
        Extrahiert eine Archivdatei (zip, tar, tar.gz, tar.bz2, tar.xz) in ein angegebenes Verzeichnis.

        :param archive_path: Pfad zur Archivdatei
        :param extract_to: Zielverzeichnis für die extrahierten Dateien
        :return: True, wenn erfolgreich, sonst False
        """
        archive_path = self._convert_path(archive_path, True)
        target_folder = self._convert_path(target_folder, True)
        ext = self.get_file_extension(archive_path)

        if not ext in self.get_supported_archive_extensions():
            logging.error("Fehler: Nicht unterstütztes Archivformat.")
            return False

        if not self.file_exist(archive_path):
            logging.error(f"Fehler: Archiv {archive_path} existiert nicht.")
            return False
        # create output folder
        if not self.folder_exists(target_folder):
            if not self.create_folder(target_folder):
                return False
        try:
            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(target_folder)
            elif tarfile.is_tarfile(archive_path):
                with tarfile.open(archive_path, 'r') as tar_ref:
                    tar_ref.extractall(target_folder)
            else:
                logging.error("Fehler: Nicht unterstütztes Archivformat.")
                return False
            return True
        except Exception as e:
            logging.error(f"Fehler beim Extrahieren: {e}")
            return False

    def pack_folder(self, source_folder: str, archive_path: str) -> None:
        """
        Packt den Inhalt eines Ordners in ein ZIP- oder TAR-Archiv, basierend auf der Dateiendung.

        Unterstützte Formate:
        - .zip
        - .tar
        - .tar.gz
        - .tar.bz2
        - .tar.xz

        :param source_folder: Pfad zum Quellordner.
        :param archive_path: Pfad zur Zieldatei (inkl. gewünschter Endung).
        """
        archive_path = self._convert_path(archive_path, True)
        source_folder = self._convert_path(source_folder, True)
        if not self.folder_exists(source_folder):
            logging.error("Can't archive folder! Folder does not exist!")
            return False
        ext = self.get_file_extension(archive_path)
        if ext == "zip":
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(source_folder):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, source_folder)
                        zipf.write(file_path, arcname)
        elif ext in self.get_supported_archive_extensions():
            mode = "w"
            if ext in ("tar.gz", ".tgz"):
                mode = "w:gz"
            elif ext in ("tar.bz2", ".tbz2"):
                mode = "w:bz2"
            elif ext in ("tar.xz", ".txz"):
                mode = "w:xz"
            with tarfile.open(archive_path, mode) as tarf:
                tarf.add(source_folder, arcname=os.path.basename(source_folder))
        else:
            logging.error(
                "Ungültiges Archivformat! Unterstützte Formate: zip, tar, tar.gz, tar.bz2, tar.xz")
            return False
        return True

    @staticmethod
    def get_supported_archive_extensions() -> list[str]:
        return ["zip", "tar", "gz", "tar.gz", "tgz", "tar.bz2", "tbz2", "tar.xz", "txz"]

    def is_archive_file(self, file_path: str) -> bool:
        ''' Checks if the file on given path is an archive file '''
        return any(file_path.endswith(ext) for ext in self.get_supported_archive_extensions())

    def download_file(self, url: str, output_file: str, timeout: int = 10) -> bool:
        """
        Downloads a file from a given URL and saves it to the specified output file.

        Args:
            url (str): The URL of the file to download
            output_file (str): The absolute path where the file should be saved
            timeout (int, optional): Request timeout in seconds. Defaults to 10.

        Returns:
            bool: True if the file was successfully downloaded, False otherwise
        """
        output_file = self._convert_path(output_file, True)
        try:
            response = requests.get(url, stream=True, timeout=timeout)
            # log error if there is one before raise_for_status()
            if not response.ok:
                error_msg = f"HTTP {response.status_code} Error for GET {url}"
                try:
                    # Try to parse json
                    error_detail = response.json()
                    error_msg += f" - Response: {error_detail}"
                except:
                    # Fallback to plain text
                    error_msg += f" - Response: {response.text}"
                logging.error(error_msg)

            response.raise_for_status()  # Raise an error for bad HTTP status codes

            # Ensure the output directory exists
            os.makedirs(os.path.dirname(output_file), exist_ok=True)

            # Write file in chunks to prevent memory overload
            with open(output_file, "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)

            logging.debug(f"File successfully downloaded: {output_file}")
            return True

        except requests.RequestException as e:
            logging.error(f"Error downloading file: {e}")
            return False

    def get_available_disk_space_gb(self, folder_path: str, kind: str = "free") -> float:
        """
        Returns the disk space in GB for the given folder path.

        Args:
            folder_path (str): The path to the folder.
            kind (str): The kind of disk space to retrieve. One of 'total', 'used', or 'free'.

        Returns:
            float: The requested disk space in GB.
        """
        if kind not in ("total", "used", "free"):
            raise ValueError("kind must be one of 'total', 'used', or 'free'")

        path = self._convert_path(folder_path, True)
        total, used, free = shutil.disk_usage(path)

        space_map = {
            "total": total,
            "used": used,
            "free": free
        }

        return space_map[kind] / (1024 ** 3)

    def get_file_checksum(self, file_path: str) -> str | None:
        """
        Calculates the MD5 checksum of a file.

        Args:
            file_path (str): The path to the file for which the checksum is to be calculated.

        Returns:
            str | None: The MD5 checksum as a hex string if the file exists, otherwise None.
        """
        file_path = self._convert_path(file_path, True)
        if not self.file_exist(file_path):
            return None
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def get_folder_for_file(self, file_path: str) -> str:
        """
        Returns the folder path for a given file path without path separator at the end.

        Args:
            file_path (str): The path to the file.

        Returns:
            str: The folder path.
        """
        file_path = self._convert_path(file_path, True)
        return os.path.dirname(file_path)

    def generate_tempfile_in_folder(self, folder: str, suffix, asRelative: bool = False, path_only: bool = False) -> str:
        """
        Generates a unique temporary file within given folder

        Args:
            folder (str): The folder in which to create the temporary file.
            suffix (str): The file extension for the temporary file.
            asRelative (bool): Whether to return the path as relative.
            path_only (bool): Whether to delete the file after getting the path.

        """
        # create temp folder if not existing
        folder_path = self._convert_path(folder, True)
        if not self.folder_exists(folder_path):
            raise Exception("Error in generate_tempfile_in_folder! Target folder does not exists")

        if (not "." in suffix):
            suffix = "."+suffix

        # Erstelle eine temporäre Datei mit einem einzigartigen Namen
        temp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=folder_path)

        # Schließe die Datei, damit sie von anderen Prozessen verwendet werden kann
        temp_file.close()
        tmpFilePath = temp_file.name if not asRelative else self.convert_absolute_path_to_relative(temp_file.name)

        # if path only delete the temp file
        if path_only:
            self.delete_file(tmpFilePath)
        return tmpFilePath
