import logging, os, re
from datetime import datetime
from modules import plex, util
from modules.util import Failed, ImageData
from plexapi.exceptions import NotFound
from ruamel import yaml

logger = logging.getLogger("Plex Meta Manager")

github_base = "https://raw.githubusercontent.com/meisnate12/Plex-Meta-Manager-Configs/master/"


def get_dict(attribute, attr_data, check_list=None):
    if check_list is None:
        check_list = []
    if attr_data and attribute in attr_data:
        if attr_data[attribute]:
            if isinstance(attr_data[attribute], dict):
                new_dict = {}
                for _name, _data in attr_data[attribute].items():
                    if _name in check_list:
                        logger.error(
                            f"Config Warning: Skipping duplicate {attribute[:-1] if attribute[-1] == 's' else attribute}: {_name}")
                    elif _data is None:
                        logger.error(
                            f"Config Warning: {attribute[:-1] if attribute[-1] == 's' else attribute}: {_name} has no data")
                    elif not isinstance(_data, dict):
                        logger.error(
                            f"Config Warning: {attribute[:-1] if attribute[-1] == 's' else attribute}: {_name} must be a dictionary")
                    else:
                        new_dict[str(_name)] = _data
                return new_dict
            else:
                logger.warning(f"Config Warning: {attribute} must be a dictionary")
        else:
            logger.warning(f"Config Warning: {attribute} attribute is blank")
    return None


class DataFile:
    def __init__(self, config, file_type, path):
        self.config = config
        self.type = file_type
        self.path = path
        self.data_type = ""
        self.templates = {}

    def load_file(self):
        try:
            if self.type in ["URL", "Git"]:
                content_path = self.path if self.type == "URL" else f"{github_base}{self.path}.yml"
                response = self.config.get(content_path)
                if response.status_code >= 400:
                    raise Failed(f"URL Error: No file found at {content_path}")
                content = response.content
            elif os.path.exists(os.path.abspath(self.path)):
                content = open(self.path, encoding="utf-8")
            else:
                raise Failed(f"File Error: File does not exist {os.path.abspath(self.path)}")
            data, _, _ = yaml.util.load_yaml_guess_indent(content)
            return data
        except yaml.scanner.ScannerError as ye:
            raise Failed(f"YAML Error: {util.tab_new_lines(ye)}")
        except Exception as e:
            util.print_stacktrace()
            raise Failed(f"YAML Error: {e}")

    def apply_template(self, name, data, template):
        if not self.templates:
            raise Failed(f"{self.data_type} Error: No templates found")
        elif not template:
            raise Failed(f"{self.data_type} Error: template attribute is blank")
        else:
            logger.debug(f"Value: {template}")
            for variables in util.get_list(template, split=False):
                if not isinstance(variables, dict):
                    raise Failed(f"{self.data_type} Error: template attribute is not a dictionary")
                elif "name" not in variables:
                    raise Failed(f"{self.data_type} Error: template sub-attribute name is required")
                elif not variables["name"]:
                    raise Failed(f"{self.data_type} Error: template sub-attribute name is blank")
                elif variables["name"] not in self.templates:
                    raise Failed(f"{self.data_type} Error: template {variables['name']} not found")
                elif not isinstance(self.templates[variables["name"]], dict):
                    raise Failed(f"{self.data_type} Error: template {variables['name']} is not a dictionary")
                else:
                    for tm in variables:
                        if not variables[tm]:
                            raise Failed(f"{self.data_type} Error: template sub-attribute {tm} is blank")
                    if self.data_type == "Collection" and "collection_name" not in variables:
                        variables["collection_name"] = str(name)
                    if self.data_type == "Playlist" and "playlist_name" not in variables:
                        variables["playlist_name"] = str(name)

                    template_name = variables["name"]
                    template = self.templates[template_name]

                    default = {}
                    if "default" in template:
                        if template["default"]:
                            if isinstance(template["default"], dict):
                                for dv in template["default"]:
                                    if template["default"][dv]:
                                        default[dv] = template["default"][dv]
                                    else:
                                        raise Failed(f"{self.data_type} Error: template default sub-attribute {dv} is blank")
                            else:
                                raise Failed(f"{self.data_type} Error: template sub-attribute default is not a dictionary")
                        else:
                            raise Failed(f"{self.data_type} Error: template sub-attribute default is blank")

                    optional = []
                    if "optional" in template:
                        if template["optional"]:
                            for op in util.get_list(template["optional"]):
                                if op not in default:
                                    optional.append(str(op))
                                else:
                                    logger.warning(f"Template Warning: variable {op} cannot be optional if it has a default")
                        else:
                            raise Failed(f"{self.data_type} Error: template sub-attribute optional is blank")

                    if "move_prefix" in template or "move_collection_prefix" in template:
                        prefix = None
                        if "move_prefix" in template:
                            prefix = template["move_prefix"]
                        elif "move_collection_prefix" in template:
                            logger.warning(f"{self.data_type} Error: template sub-attribute move_collection_prefix will run as move_prefix")
                            prefix = template["move_collection_prefix"]
                        if prefix:
                            for op in util.get_list(prefix):
                                variables["collection_name"] = variables["collection_name"].replace(f"{str(op).strip()} ", "") + f", {str(op).strip()}"
                        else:
                            raise Failed(f"{self.data_type} Error: template sub-attribute move_prefix is blank")

                    def check_data(_method, _data):
                        if isinstance(_data, dict):
                            final_data = {}
                            for sm, sd in _data.items():
                                try:
                                    final_data[sm] = check_data(_method, sd)
                                except Failed:
                                    continue
                        elif isinstance(_data, list):
                            final_data = []
                            for li in _data:
                                try:
                                    final_data.append(check_data(_method, li))
                                except Failed:
                                    continue
                        else:
                            txt = str(_data)

                            def scan_text(og_txt, var, var_value):
                                if og_txt == f"<<{var}>>":
                                    return str(var_value)
                                elif f"<<{var}>>" in str(og_txt):
                                    return str(og_txt).replace(f"<<{var}>>", str(var_value))
                                else:
                                    return og_txt

                            for option in optional:
                                if option not in variables and f"<<{option}>>" in txt:
                                    raise Failed
                            for variable, variable_data in variables.items():
                                if (variable == "collection_name" or variable == "playlist_name") and _method in ["radarr_tag", "item_radarr_tag", "sonarr_tag", "item_sonarr_tag"]:
                                    txt = scan_text(txt, variable, variable_data.replace(",", ""))
                                elif variable != "name":
                                    txt = scan_text(txt, variable, variable_data)
                            for dm, dd in default.items():
                                txt = scan_text(txt, dm, dd)
                            if txt in ["true", "True"]:
                                final_data = True
                            elif txt in ["false", "False"]:
                                final_data = False
                            else:
                                try:
                                    num_data = float(txt)
                                    final_data = int(num_data) if num_data.is_integer() else num_data
                                except (ValueError, TypeError):
                                    final_data = txt
                        return final_data

                    new_attributes = {}
                    for method_name, attr_data in template.items():
                        if method_name not in data and method_name not in ["default", "optional", "move_collection_prefix", "move_prefix"]:
                            if attr_data is None:
                                logger.error(f"Template Error: template attribute {method_name} is blank")
                                continue
                            try:
                                new_attributes[method_name] = check_data(method_name, attr_data)
                            except Failed:
                                continue
                    return new_attributes


class MetadataFile(DataFile):
    def __init__(self, config, library, file_type, path):
        super().__init__(config, file_type, path)
        self.data_type = "Collection"
        self.library = library
        if file_type == "Data":
            self.metadata = None
            self.collections = get_dict("collections", path, library.collections)
            self.templates = get_dict("templates", path)
        else:
            logger.info("")
            logger.info(f"Loading Metadata {file_type}: {path}")
            data = self.load_file()
            self.metadata = get_dict("metadata", data, library.metadatas)
            self.templates = get_dict("templates", data)
            self.collections = get_dict("collections", data, library.collections)

            if self.metadata is None and self.collections is None:
                raise Failed("YAML Error: metadata or collections attribute is required")
            logger.info(f"Metadata File Loaded Successfully")

    def get_collections(self, requested_collections):
        if requested_collections:
            return {c: self.collections[c] for c in util.get_list(requested_collections) if c in self.collections}
        else:
            return self.collections

    def update_metadata(self):
        if not self.metadata:
            return None
        logger.info("")
        util.separator("Running Metadata")
        logger.info("")
        for mapping_name, meta in self.metadata.items():
            methods = {mm.lower(): mm for mm in meta}
            if self.config.test_mode and ("test" not in methods or meta[methods["test"]] is not True):
                continue

            updated = False
            edits = {}
            advance_edits = {}

            def add_edit(name, current_item, group, alias, key=None, value=None, var_type="str"):
                if value or name in alias:
                    if value or group[alias[name]]:
                        if key is None:         key = name
                        if value is None:       value = group[alias[name]]
                        try:
                            current = str(getattr(current_item, key, ""))
                            if var_type == "date":
                                final_value = util.validate_date(value, name, return_as="%Y-%m-%d")
                                current = current[:-9]
                            elif var_type == "float":
                                if value is None:
                                    raise Failed(f"Metadata Error: {name} attribute is blank")
                                final_value = None
                                try:
                                    value = float(str(value))
                                    if 0 <= value <= 10:
                                        final_value = value
                                except ValueError:
                                    pass
                                if final_value is None:
                                    raise Failed(f"Metadata Error: {name} attribute must be a number between 0 and 10")
                            else:
                                final_value = value
                            if current != str(final_value):
                                edits[f"{key}.value"] = final_value
                                edits[f"{key}.locked"] = 1
                                logger.info(f"Detail: {name} updated to {final_value}")
                        except Failed as ee:
                            logger.error(ee)
                    else:
                        logger.error(f"Metadata Error: {name} attribute is blank")

            def add_advanced_edit(attr, obj, group, alias, show_library=False, new_agent=False):
                key, options = plex.item_advance_keys[f"item_{attr}"]
                if attr in alias:
                    if new_agent and self.library.agent not in plex.new_plex_agents:
                        logger.error(f"Metadata Error: {attr} attribute only works for with the New Plex Movie Agent and New Plex TV Agent")
                    elif show_library and not self.library.is_show:
                        logger.error(f"Metadata Error: {attr} attribute only works for show libraries")
                    elif group[alias[attr]]:
                        method_data = str(group[alias[attr]]).lower()
                        if method_data not in options:
                            logger.error(f"Metadata Error: {group[alias[attr]]} {attr} attribute invalid")
                        elif getattr(obj, key) != options[method_data]:
                            advance_edits[key] = options[method_data]
                            logger.info(f"Detail: {attr} updated to {method_data}")
                    else:
                        logger.error(f"Metadata Error: {attr} attribute is blank")

            def edit_tags(attr, obj, group, alias, extra=None, movie_library=False):
                if movie_library and not self.library.is_movie and (attr in alias or f"{attr}.sync" in alias or f"{attr}.remove" in alias):
                    logger.error(f"Metadata Error: {attr} attribute only works for movie libraries")
                elif attr in alias and f"{attr}.sync" in alias:
                    logger.error(f"Metadata Error: Cannot use {attr} and {attr}.sync together")
                elif f"{attr}.remove" in alias and f"{attr}.sync" in alias:
                    logger.error(f"Metadata Error: Cannot use {attr}.remove and {attr}.sync together")
                elif attr in alias and group[alias[attr]] is None:
                    logger.error(f"Metadata Error: {attr} attribute is blank")
                elif f"{attr}.remove" in alias and group[alias[f"{attr}.remove"]] is None:
                    logger.error(f"Metadata Error: {attr}.remove attribute is blank")
                elif f"{attr}.sync" in alias and group[alias[f"{attr}.sync"]] is None:
                    logger.error(f"Metadata Error: {attr}.sync attribute is blank")
                elif attr in alias or f"{attr}.remove" in alias or f"{attr}.sync" in alias:
                    add_tags = util.get_list(group[alias[attr]]) if attr in alias else []
                    if extra:
                        add_tags.extend(extra)
                    remove_tags = util.get_list(group[alias[f"{attr}.remove"]]) if f"{attr}.remove" in alias else None
                    sync_tags = util.get_list(group[alias[f"{attr}.sync"]] if group[alias[f"{attr}.sync"]] else []) if f"{attr}.sync" in alias else None
                    return self.library.edit_tags(attr, obj, add_tags=add_tags, remove_tags=remove_tags, sync_tags=sync_tags)
                return False

            def set_image(attr, group, alias, is_poster=True, is_url=True):
                if group[alias[attr]]:
                    return ImageData(attr, group[alias[attr]], is_poster=is_poster, is_url=is_url)
                else:
                    logger.error(f"Metadata Error: {attr} attribute is blank")

            def set_images(obj, group, alias):
                poster = None
                background = None
                if "url_poster" in alias:
                    poster = set_image("url_poster", group, alias)
                elif "file_poster" in alias:
                    poster = set_image("file_poster", group, alias, is_url=False)
                if "url_background" in alias:
                    background = set_image("url_background", group, alias, is_poster=False)
                elif "file_background" in alias:
                    background = set_image("file_background", group, alias, is_poster=False, is_url=False)

                if poster or background:
                    self.library.upload_images(obj, poster=poster, background=background)

            logger.info("")
            util.separator()
            logger.info("")
            year = None
            if "year" in methods:
                next_year = datetime.now().year + 1
                if meta[methods["year"]] is None:
                    raise Failed("Metadata Error: year attribute is blank")
                try:
                    year_value = int(str(meta[methods["year"]]))
                    if 1800 <= year_value <= next_year:
                        year = year_value
                except ValueError:
                    pass
                if year is None:
                    raise Failed(f"Metadata Error: year attribute must be an integer between 1800 and {next_year}")

            title = mapping_name
            if "title" in methods:
                if meta[methods["title"]] is None:
                    logger.error("Metadata Error: title attribute is blank")
                else:
                    title = meta[methods["title"]]

            item = self.library.search_item(title, year=year)

            if item is None:
                item = self.library.search_item(f"{title} (SUB)", year=year)

            if item is None and "alt_title" in methods:
                if meta[methods["alt_title"]] is None:
                    logger.error("Metadata Error: alt_title attribute is blank")
                else:
                    alt_title = meta["alt_title"]
                    item = self.library.search_item(alt_title, year=year)

            if item is None:
                logger.error(f"Plex Error: Item {mapping_name} not found")
                logger.error(f"Skipping {mapping_name}")
                continue

            item_type = "Movie" if self.library.is_movie else "Show"
            logger.info(f"Updating {item_type}: {title}...")

            tmdb_item = None
            tmdb_is_movie = None
            if ("tmdb_show" in methods or "tmdb_id" in methods) and "tmdb_movie" in methods:
                logger.error("Metadata Error: Cannot use tmdb_movie and tmdb_show when editing the same metadata item")

            if "tmdb_show" in methods or "tmdb_id" in methods or "tmdb_movie" in methods:
                try:
                    if "tmdb_show" in methods or "tmdb_id" in methods:
                        data = meta[methods["tmdb_show" if "tmdb_show" in methods else "tmdb_id"]]
                        if data is None:
                            logger.error("Metadata Error: tmdb_show attribute is blank")
                        else:
                            tmdb_is_movie = False
                            tmdb_item = self.config.TMDb.get_show(util.regex_first_int(data, "Show"))
                    elif "tmdb_movie" in methods:
                        if meta[methods["tmdb_movie"]] is None:
                            logger.error("Metadata Error: tmdb_movie attribute is blank")
                        else:
                            tmdb_is_movie = True
                            tmdb_item = self.config.TMDb.get_movie(util.regex_first_int(meta[methods["tmdb_movie"]], "Movie"))
                except Failed as e:
                    logger.error(e)

            originally_available = None
            original_title = None
            rating = None
            studio = None
            tagline = None
            summary = None
            genres = []
            if tmdb_item:
                originally_available = tmdb_item.release_date if tmdb_is_movie else tmdb_item.first_air_date
                if tmdb_item and tmdb_is_movie is True and tmdb_item.original_title != tmdb_item.title:
                    original_title = tmdb_item.original_title
                elif tmdb_item and tmdb_is_movie is False and tmdb_item.original_name != tmdb_item.name:
                    original_title = tmdb_item.original_name
                rating = tmdb_item.vote_average
                if tmdb_is_movie is True and tmdb_item.production_companies:
                    studio = tmdb_item.production_companies[0].name
                elif tmdb_is_movie is False and tmdb_item.networks:
                    studio = tmdb_item.networks[0].name
                tagline = tmdb_item.tagline if len(tmdb_item.tagline) > 0 else None
                summary = tmdb_item.overview
                genres = [genre.name for genre in tmdb_item.genres]

            edits = {}
            add_edit("title", item, meta, methods, value=title)
            add_edit("sort_title", item, meta, methods, key="titleSort")
            add_edit("originally_available", item, meta, methods, key="originallyAvailableAt", value=originally_available, var_type="date")
            add_edit("critic_rating", item, meta, methods, value=rating, key="rating", var_type="float")
            add_edit("audience_rating", item, meta, methods, key="audienceRating", var_type="float")
            add_edit("user_rating", item, meta, methods, key="userRating", var_type="float")
            add_edit("content_rating", item, meta, methods, key="contentRating")
            add_edit("original_title", item, meta, methods, key="originalTitle", value=original_title)
            add_edit("studio", item, meta, methods, value=studio)
            add_edit("tagline", item, meta, methods, value=tagline)
            add_edit("summary", item, meta, methods, value=summary)
            if self.library.edit_item(item, mapping_name, item_type, edits):
                updated = True

            advance_edits = {}
            for advance_edit in ["episode_sorting", "keep_episodes", "delete_episodes", "season_display", "episode_ordering", "metadata_language", "use_original_title"]:
                is_show = advance_edit in ["episode_sorting", "keep_episodes", "delete_episodes", "season_display", "episode_ordering"]
                is_new_agent = advance_edit in ["metadata_language", "use_original_title"]
                add_advanced_edit(advance_edit, item, meta, methods, show_library=is_show, new_agent=is_new_agent)
            if self.library.edit_item(item, mapping_name, item_type, advance_edits, advanced=True):
                updated = True

            for tag_edit in ["genre", "label", "collection", "country", "director", "producer", "writer"]:
                is_movie = tag_edit in ["country", "director", "producer", "writer"]
                has_extra = genres if tag_edit == "genre" else None
                if edit_tags(tag_edit, item, meta, methods, movie_library=is_movie, extra=has_extra):
                    updated = True

            logger.info(f"{item_type}: {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")

            set_images(item, meta, methods)

            if "seasons" in methods and self.library.is_show:
                if meta[methods["seasons"]]:
                    for season_id in meta[methods["seasons"]]:
                        updated = False
                        logger.info("")
                        logger.info(f"Updating season {season_id} of {mapping_name}...")
                        if isinstance(season_id, int):
                            season = None
                            for s in item.seasons():
                                if s.index == season_id:
                                    season = s
                                    break
                            if season is None:
                                logger.error(f"Metadata Error: Season: {season_id} not found")
                            else:
                                season_dict = meta[methods["seasons"]][season_id]
                                season_methods = {sm.lower(): sm for sm in season_dict}

                                if "title" in season_methods and season_dict[season_methods["title"]]:
                                    title = season_dict[season_methods["title"]]
                                else:
                                    title = season.title
                                if "sub" in season_methods:
                                    if season_dict[season_methods["sub"]] is None:
                                        logger.error("Metadata Error: sub attribute is blank")
                                    elif season_dict[season_methods["sub"]] is True and "(SUB)" not in title:
                                        title = f"{title} (SUB)"
                                    elif season_dict[season_methods["sub"]] is False and title.endswith(" (SUB)"):
                                        title = title[:-6]
                                    else:
                                        logger.error("Metadata Error: sub attribute must be True or False")

                                edits = {}
                                add_edit("title", season, season_dict, season_methods, value=title)
                                add_edit("summary", season, season_dict, season_methods)
                                if self.library.edit_item(season, season_id, "Season", edits):
                                    updated = True
                                set_images(season, season_dict, season_methods)
                        else:
                            logger.error(f"Metadata Error: Season: {season_id} invalid, it must be an integer")
                        logger.info(f"Season {season_id} of {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")
                else:
                    logger.error("Metadata Error: seasons attribute is blank")
            elif "seasons" in methods:
                logger.error("Metadata Error: seasons attribute only works for show libraries")

            if "episodes" in methods and self.library.is_show:
                if meta[methods["episodes"]]:
                    for episode_str in meta[methods["episodes"]]:
                        updated = False
                        logger.info("")
                        match = re.search("[Ss]\\d+[Ee]\\d+", episode_str)
                        if match:
                            output = match.group(0)[1:].split("E" if "E" in match.group(0) else "e")
                            season_id = int(output[0])
                            episode_id = int(output[1])
                            logger.info(f"Updating episode S{season_id}E{episode_id} of {mapping_name}...")
                            try:
                                episode = item.episode(season=season_id, episode=episode_id)
                            except NotFound:
                                logger.error(f"Metadata Error: episode {episode_id} of season {season_id} not found")
                            else:
                                episode_dict = meta[methods["episodes"]][episode_str]
                                episode_methods = {em.lower(): em for em in episode_dict}

                                if "title" in episode_methods and episode_dict[episode_methods["title"]]:
                                    title = episode_dict[episode_methods["title"]]
                                else:
                                    title = episode.title
                                if "sub" in episode_dict:
                                    if episode_dict[episode_methods["sub"]] is None:
                                        logger.error("Metadata Error: sub attribute is blank")
                                    elif episode_dict[episode_methods["sub"]] is True and "(SUB)" not in title:
                                        title = f"{title} (SUB)"
                                    elif episode_dict[episode_methods["sub"]] is False and title.endswith(" (SUB)"):
                                        title = title[:-6]
                                    else:
                                        logger.error("Metadata Error: sub attribute must be True or False")
                                edits = {}
                                add_edit("title", episode, episode_dict, episode_methods, value=title)
                                add_edit("sort_title", episode, episode_dict, episode_methods, key="titleSort")
                                add_edit("rating", episode, episode_dict, episode_methods, var_type="float")
                                add_edit("originally_available", episode, episode_dict, episode_methods, key="originallyAvailableAt", var_type="date")
                                add_edit("summary", episode, episode_dict, episode_methods)
                                if self.library.edit_item(episode, f"{season_id} Episode: {episode_id}", "Season", edits):
                                    updated = True
                                if edit_tags("director", episode, episode_dict, episode_methods):
                                    updated = True
                                if edit_tags("writer", episode, episode_dict, episode_methods):
                                    updated = True
                                set_images(episode, episode_dict, episode_methods)
                            logger.info(f"Episode S{season_id}E{episode_id} of {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")
                        else:
                            logger.error(f"Metadata Error: episode {episode_str} invalid must have S##E## format")
                else:
                    logger.error("Metadata Error: episodes attribute is blank")
            elif "episodes" in methods:
                logger.error("Metadata Error: episodes attribute only works for show libraries")


class PlaylistFile(DataFile):
    def __init__(self, config, file_type, path):
        super().__init__(config, file_type, path)
        self.data_type = "Playlist"
        self.playlists = {}
        logger.info("")
        logger.info(f"Loading Playlist File {file_type}: {path}")
        data = self.load_file()
        self.playlists = get_dict("playlists", data, self.config.playlist_names)
        self.templates = get_dict("templates", data)
        if not self.playlists:
            raise Failed("YAML Error: playlists attribute is required")
        logger.info(f"Playlist File Loaded Successfully")
