import os
import re
import requests
import webbrowser
from datetime import datetime
from xml.etree.ElementTree import Element, ElementTree, SubElement
from bs4 import BeautifulSoup

import config

# Extracts a verse number and text.
re_verse_number = re.compile(r"^(\d)*\.?\s*(.*)")
# Unnecessary content in copyright notices, including anything in parentheses.
re_copyright_fluff = re.compile(r"(Copyright|all rights reserved|used by permission|\.|\([^)]*\))", re.IGNORECASE)
re_author_fluff = re.compile(r"\b(words|music)\b", re.IGNORECASE)


class PraiseScraper:
    def __init__(self, out_folder) -> None:
        self.out_folder = out_folder
        self.session = requests.Session()
        self.song_num = None
        self.title = None
        self.authors = []
        self.themes = []
        self.copyright = None
        self.bridge_num = 0
        self.chorus_num = 0
        self.verse_order = []
        self.tree = None
        self.verse_order_el = None
        self.authors_el = None
        self.url = None
        self.filename = None

    def login(self, username, password):

        # Get the login page and extract the login nonce.
        response = self.session.get("https://www.praise.org.uk/my-account/")
        soup = BeautifulSoup(response.text, "html.parser")
        nonce = soup.find("input", id="woocommerce-login-nonce").attrs["value"]

        # Post the login credentials to the login page.
        credentials = {"username": username, "password": password, "woocommerce-login-nonce": nonce, "login": "Log+in"}
        self.session.post("https://www.praise.org.uk/my-account/", credentials)

    def download_song(self, song_num: str):

        html = self._get_song(song_num)
        soup = BeautifulSoup(html, "html.parser")
        title_tag = self._get_title(soup)
        related = soup.find("h2", string="Related Information")
        self._get_authors(related)
        self._get_themes(related)
        self._get_copyright(related)

        # Construct the output XML.
        root_el = self._create_tree()
        properties_el = self._create_properties(root_el)
        self._create_authors(properties_el)
        self._create_themes(properties_el)
        self._create_songbooks(properties_el)
        self._create_lyrics(title_tag, root_el)
        self.verse_order_el.text = " ".join(self.verse_order)

        self._write_output_file()

    def _get_song(self, song_num: str) -> str:
        self.song_num = song_num

        # Send a search request for the song.
        response = self.session.get(f"https://www.praise.org.uk/?s={song_num}&post_type=hymn")
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract the first search result.
        self.url = soup.select_one("table.search-results tr td a")["href"]

        # Get the song page.
        response = self.session.get(self.url)
        return response.text

    def _get_title(self, soup):
        title_tag = soup.select_one("div.main-content div.textual h2")
        self.title = title_tag.string
        return title_tag

    def _get_authors(self, related):
        author_label = related.find_next("strong", string="Author:")
        if author_label is not None:
            authors = author_label.find_next_sibling("a")
            self.authors = [a.strip() for a in re.split(",|and|&", authors.string)]
        else:
            authors_label = related.find_next("strong", string="Authors:")
            if authors_label is not None:
                self.authors = [a.string for a in authors_label.find_next_siblings("a")]

    def _get_themes(self, related):
        themes_label = related.find_next("strong", string="Themes:")
        if themes_label is not None:
            self.themes = [a.string for a in themes_label.find_next_siblings("a")]

    def _get_copyright(self, related):
        copyright_label = related.find_next("strong", string="Copyright:")
        if copyright_label is None:
            self.copyright = "Public Domain"
        else:
            copyright = copyright_label.next_sibling.string
            self.copyright = re_copyright_fluff.sub("", copyright).strip().title()

    def _create_tree(self):
        root_el = Element(
            "song",
            {
                "xmlns": "http://openlyrics.info/namespace/2009/song",
                "version": "0.8",
                "createdIn": "PraiseOpenLyrics",
                "createdDate": datetime.now().replace(microsecond=0).isoformat(),
            },
        )
        root_el.text = "\n"
        self.tree = ElementTree(root_el)
        return root_el

    def _create_properties(self, root_el):
        properties_el = self._create_element(root_el, "properties")
        titles_el = self._create_element(properties_el, "titles")
        self._create_element(titles_el, "title", self.title)
        self._create_element(properties_el, "copyright", self.copyright)
        self.verse_order_el = self._create_element(properties_el, "verseOrder")
        return properties_el

    def _create_authors(self, properties_el):
        authors_el = self._create_element(properties_el, "authors")
        for author in self.authors:
            # Don't use the author text if there is is more than just a name.
            if re_author_fluff.search(author) is not None:
                self._create_element(authors_el, "author")
                continue
            # Remove dates
            author = re.sub(r"\d+-?\d+$", "", author).rstrip()
            # rearrange "surname, forename"
            if author.count(",") == 1:
                surname, forename = author.split(",")
                author = forename.lstrip() + " " + surname.rstrip()
            self._create_element(authors_el, "author", author)

    def _create_themes(self, properties_el):
        if len(self.themes):
            themes_el = self._create_element(properties_el, "themes")
            for theme in self.themes:
                self._create_element(themes_el, "theme", theme)

    def _create_songbooks(self, properties_el):
        songbooks_el = self._create_element(properties_el, "songbooks")
        SubElement(songbooks_el, "songbook", {"name": "Praise!", "entry": self.song_num}).tail = "\n"

    def _create_lyrics(self, title_tag, root_el):
        lyrics_el = self._create_element(root_el, "lyrics")
        verses = title_tag.find_next_siblings("p")
        self._create_verses(verses, lyrics_el)

    def _create_element(self, parent: Element, tag: str, text="\n") -> Element:
        element = SubElement(parent, tag)
        element.text = text
        element.tail = "\n"
        return element

    def _create_verses(self, verses, lyrics_el):
        for verse_idx, verse in enumerate(verses):
            lines = [line for line in verse.stripped_strings]
            if len(lines) == 0:
                continue
            if verse_idx == 0:
                # Praise! doesn't number the first verse.
                verse_num = 1
                # Praise! uppercases the first line. If the title is the same
                # as the first line apart from case, then use the title.
                first_line = lines[0]
                if first_line.lower().startswith(self.title.lower()):
                    # Substitute, preserving any trailing punctuation.
                    lines[0] = self.title + first_line[len(self.title):]
            else:
                # Split the verse number from the first line of the verse.
                verse_num, lines[0] = re_verse_number.search(lines[0]).groups()

            self._create_verse(verse_num, lines, lyrics_el)

    def _create_verse(self, verse_num, lines, lyrics_el):
        is_chorus = False
        if verse_num:
            # It's a verse.
            name = f"v{verse_num}"
        elif lines[0].lower() == "bridge:":
            # It's a bridge.
            lines = lines[1:]
            self.bridge_num += 1
            name = name = f"b{self.bridge_num}"
        else:
            # It's a chorus.
            # Is it a chorus repeat?
            if "…" in lines[0] and self.chorus_num:
                # Just add the previous chorus to the verse order.
                self.verse_order.append(f"c{self.chorus_num}")
                # Deal with cases where the chorus repeat is at the start
                # of a verse paragraph.
                lines = lines[1:]
                if len(lines) == 0:
                    # Done with this verse.
                    return
                verse_num, lines[0] = re_verse_number.search(lines[0]).groups()
                if verse_num is not None:
                    # Process as a verse.
                    name = f"v{verse_num}"
                elif lines[0].lower() == "bridge:":
                    # Process as a bridge.
                    lines = lines[1:]
                    self.bridge_num += 1
                    name = f"b{self.bridge_num}"
                else:
                    return
            else:
                # It's a full chorus.
                if lines[0].lower() == "chorus:":
                    lines = lines[1:]
                is_chorus = True
                self.chorus_num += 1
                name = f"c{self.chorus_num}"

        verse_el = self._create_element(lyrics_el, "verse")
        verse_el.set("name", name)
        self.verse_order.append(name)
        lines_el, br_el = self._create_lines(lines, verse_el)

        if is_chorus:
            self._italicise_chorus(lines_el, br_el)

    def _italicise_chorus(self, lines_el, br_el):
        # Add {it} at the start of the first line
        lines_el.text = "{it}" + lines_el.text
        # Add {/it} at the end of the last line.
        if br_el is None:
            lines_el.text += "{/it}"
        else:
            br_el.tail += "{/it}"

    def _create_lines(self, lines, verse_el):
        lines_el = self._create_element(verse_el, "lines")
        br_el = None
        for line_idx, line in enumerate(lines):
            num, line = re_verse_number.search(line).groups()
            if num is not None:
                lines[line_idx] = line
                self._create_verse(num, lines[line_idx:])
                break

            if line_idx == 0:
                # Add the first line as the <lines> element text.
                lines_el.text = line
            else:
                # For subsequent lines, add a <br/> element with the line
                # as the tail.
                br_el = SubElement(lines_el, "br")
                br_el.tail = line
        return lines_el, br_el

    def _write_output_file(self):
        self.filename = self.song_num + "_" + re.sub(r"\W", "_", self.title) + ".xml"
        file_path = os.path.join(self.out_folder, self.filename)
        file_path = os.path.realpath(file_path)
        self.tree.write(file_path, encoding="unicode", xml_declaration=True)


if __name__ == "__main__":

    praise = PraiseScraper(config.OUT_FOLDER)
    praise.login(config.USERNAME, config.PASSWORD)

    while True:
        song_num = input("Enter the song number (blank to quit): ")
        if not song_num.strip():
            break
        filename = praise.download_song(song_num)
        print(praise.filename)
        webbrowser.open_new_tab(praise.url)
