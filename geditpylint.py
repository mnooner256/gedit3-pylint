"""
This module contains the plugin for GeditPylint.

References:
    Python Gtk Tutorial: http://python-gtk-3-tutorial.readthedocs.org/en/latest/
    Python Gtk: http://lazka.github.io/pgi-docs/Gtk-3.0/classes/
    Gedit objects: https://developer.gnome.org/gedit/stable/
    Gtk objects: https://developer.gnome.org/gtk3/stable/
    GtkSourceView: https://developer.gnome.org/gtksourceview/stable/
    Basic tutorial: http://www.micahcarrick.com/writing-plugins-for-gedit-3-in-python.html
"""

from gi.repository import GObject, Gedit, Gdk

#Change this value to True to enable debug messages to be printed to the
#the console. Likewise, set it to False to turn of debug messages
ENABLE_DEBUG = False


class GeditPylint(GObject.Object, Gedit.WindowActivatable):
    """This object represents the "plugin" itself. Basically gedit will
    automatically ceate an instance for us.
    """
    __gtype_name__ = "GeditPylint"
    window = GObject.property(type=Gedit.Window)

    def __init__(self):
        GObject.Object.__init__(self)

        #This attribute contains a list of signal handlers. Its purpose is to
        #allow us to gracefully disconnect the signal handlers when this
        #object is decommissioned.
        self.handlers = list()

        #Gedit often emits several identical adding, loading,
        #and saving signals. This keeps us from connecting
        #signals to documents we have already examined.
        self.known_documents = list()

        #This will hold the messages retrieved by pylint.
        #They will be index by tag object. This allows for easy
        #access and lookup in the cursor-moved signal hanlder
        self.lint_messages = dict()

        #These are the colors used for the different pylint message types
        self.lint_color = dict()
        self.lint_color['F'] = Gdk.RGBA(1.0, 0.0, 0.0, 0.25)
        self.lint_color['E'] = Gdk.RGBA(1.0, 0.5, 0.5, 0.25)
        self.lint_color['W'] = Gdk.RGBA(1.0, 1.0, 0.5, 0.25)
        self.lint_color['R'] = Gdk.RGBA(0.5, 1.0, 0.5, 0.25)
        self.lint_color['C'] = Gdk.RGBA(0.5, 0.5, 1.0, 0.25)

        #This is a catch all for any code other than the one listed.
        #It is mostly used to signal an error condition inside the
        #plugin. No one should ever see this color.
        self.lint_color['O'] = Gdk.RGBA(0, 0, 0, 0.5)

        #Pango does not seem to understand that "words" in code don't
        #necessarily end with a period or whitespace. Instead they can
        #contain underscore's or hyphens, etc. This list is used to perform
        #a simple test to see if the we have selected a whole word or not.
        self.word_end_exceptions = ['-', '_']

        debug('Init!!')

    def attach_signal(self, object, signal, handler):
        """This is a helper function that allows us to attach signal handlers
        to a given object's signal. Basically it is to encapsulate the
        process so I do not have to copy-paste the logic.
        """
        debug('Connecting signal ', signal)
        hid = object.connect(signal, handler)

        #We only need to clean up and keep track of window based handlers.
        #Otherwise the object will deal with them itself.
        if self.window == object:
            self.handlers.append(hid)

    def do_activate(self):
        """This is called by Gedit when a view is activated. Generally, this
        will be called several times in a row, I don't know why. Just be
        careful about how the logic here works.

        This method, esentially, attaches a signal hanlder for the 'tab-added'
        signal. The result is that the GeditPylint.tab_added() method will
        be called every time the user opens a tab.
        """
        debug("View {} activated.".format(str(self.window)))

        self.status_bar = self.window.get_statusbar()
        self.context_id = self.status_bar.get_context_id('pylint')

        self.attach_signal(self.window, 'tab-added', self.tab_added)

    def do_deactivate(self):
        """This is called by Gedit when the plugin is being decommissioned.
        This method's job is to gracefully disconnect all the signal handlers
        that were created by do_activate().
        """
        debug("View {} deactivated.".format(str(self.window)))

        #Clean up the event handlers
        for hid in self.handlers:
            debug('Deactivating handler: ', hid)
            self.window.disconnect(hid)

    def do_update_state(self):
        """This is called, by Gedit, whenever the Window state is updated.
        This method does not do much. It essentially handles a side case
        where the user opens a file when a blank tab is already the current
        view.
        """
        debug("View {} state updated.".format(str(self.window)))

        #Gedit starts with an open blank tab, if the user
        #"opens" a document, a new tab is not created, instead
        #update_sate is emited. This should catch this situation
        #and make sure the tab is examined for lint-ing
        tab = self.window.get_active_tab()
        if tab is not None and tab.get_state() == Gedit.TabState.STATE_NORMAL:
            debug('\tView is adding tab')
            self.tab_added(self.window, tab)

    def tab_added(self, window, tab, data=None):
        """This method handles 'tab-added' signals. When a tab is added the
        underlying document inside the tab is examined. If the document's
        mime-type containes 'python' then we setup for running lint when the
        document is saved. Other mime-types are discarded.

        Python documents are only examined once. The known_documents attribute
        contains a cache of examined if the document exists there, then it
        goes unexamined. Non-python documents may be reexamined. Gedit may
        change the document's mime-type, so it is important be able to
        reexamine documents.
        """
        debug("Adding tab: ", tab)

        #This occurs if the window is shut with a blank tab open
        if not tab:
            debug('\tTab does not exist, skipping')
            return False

        #Get the tab's underlying document
        doc = tab.get_document()

        #Short circut for documents already examined
        if doc in self.known_documents:
            debug('Already know about the document, skipping')
            return False

        #Short circut for non-python files
        if 'python' not in doc.get_mime_type():
            debug('Not a python file: ', doc.get_mime_type())
            return False

        debug("Tab contains a Python file")
        self.attach_signal(doc, "saved", self.run_pylint)
        self.attach_signal(doc, 'cursor-moved', self.show_lint_message)
        self.known_documents.append(doc)

        #Give the python file an initial lint
        self.run_pylint(doc, None)

    def run_pylint(self, document, error, data=None):
        """This method runs pylint. In general this method is called as a
        signal handler for the document "saved" signal.

        Pylint is run in a subprocess. Its ouput is then parsed. The document
        is then altered to highlight the type of messages received from
        pylint.
        """
        import os.path
        import subprocess
        import sys

        debug('Running lint!')

        #Get the documents file name
        filename = document.get_location().get_path()

        #This may happen when a document is put in python mode
        #but it has yet to be saved.
        if filename is None:
            return False

        #Pylint looks for settings files in several places,
        #one of those places is along the path of the input file.
        #Hence, set the process' working directory to the file's directory.
        working_directory = os.path.dirname(filename)

        #Run pylint
        try:
            proc = subprocess.Popen(['pylint', '-r', 'n',
                                     '--msg-template={line}:{column}:'
                                     '[{msg_id} {symbol}] {msg}',
                                     filename],
                                    cwd=working_directory,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            output, _ = proc.communicate()

            if proc.returncode >= 32:
                debug('Pylint failed to run properly')
                debug('Output from pylint:')
                debug(str(output.decode()))
                return False
        except FileNotFoundError:
            print('Pylint is not installed or not found on the path',
                  file=sys.stderr)

        #Empty the document of pylint tags
        for tag in self.lint_messages.keys():
            document.get_tag_table().remove(tag)

        #Take pylints messages and parse them into tags and useful dicts.
        self.lint_parse(document, output.decode())

        for tag, status in self.lint_messages.items():
            #The document uses zero based counts
            line = int(status['line'])-1
            column = int(status['column'])

            #The tag will be applied at the given line and column from pylint
            start_iter = document.get_iter_at_line_offset(line, column)

            #We need to calculate where to end the tag. Start from the
            #start postition.
            #NOTE: The documentation says that we do not need to call the
            #copy() method. I tried that, it did not work. This may be fixed
            #in in an updated version of GTK's python bindings.
            end_iter = start_iter.copy()

            #If the message is located at column zero, take that to mean
            #that the message applies to the entire line.
            if column == 0:
                end_iter.forward_to_line_end()
            else:
                #Keep looking for the actual end of a word. The loop handles
                #variable names like_this_one or strings-like-this.
                while not end_iter.ends_line():
                    end_iter.forward_word_end()
                    if end_iter.get_char() not in self.word_end_exceptions:
                        break

            #Skip opening whitespace
            self.forward_to_char(start_iter, end_iter)

            #Tag the text in the document
            document.apply_tag(tag, start_iter, end_iter)

        return False

    def lint_parse(self, document, messages):
        """This function parses the output of pylint into a programatically
        useful form. Pylint's messages are stored in a dictionary, which
        is keyed to tags this function will create.

        Pylint ouputs a header that will be discarded. Further, pylint
        outputs lines showing where it thinks the error occured, these
        are also discarded.

        Each message will have a unique tag in the document. The tag names
        follow the form 'pylint-#' (where # represents a number). Each tag
        changes the background color of the window. The colors can be found
        in self.lint_color dictionary. The tag's color is chosen based on
        the message type.
        """
        import string

        #Empty the messages dict
        self.lint_messages.clear()

        after_header = False
        for n, message in enumerate(messages.split('\n')):
            #Read off lint's header. Look for *, after that the real
            #data begins
            if not after_header:
                if '*' == message[0] and '***' in message:
                    after_header = True
                continue

            #Skip blank lines and lines begining with white space
            if (message is None or message is '' or
               message[0] in string.whitespace):
                continue

            (line, column, status_txt) = message.split(':')

            #Look at the character after the bracket, it should be
            #one of pylint's message types
            if status_txt[1] in self.lint_color:
                color = self.lint_color[status_txt[1]]
            elif status_txt[1] == 'I':
                #This represents an ignore message. These are usually
                #comments that tell pylint to not to ignore an issue in the
                #file or on a line. We are goint to ignore them too.
                continue
            else:
                #We did not find a message type, use the error color
                color = self.lint_color['O']

            #Create a tag
            tag = document.create_tag('pylint-{}'.format(n),
                                      background_rgba=color)

            self.lint_messages[tag] = {'line': line, 'column': column,
                                       'message': status_txt}

    def forward_to_char(self, start_iter, limit_iter):
        """Since python file's lines are usually indented, we need to move
        the start position to the first non-whitespace character. We limit
        how far the iterator can go using the second parameter (usually set
        to the end of the line).

        Note: This function essentially replicates the functionality of the
        Gtk.TextIter.forward_find_char() method. I actually tried to use this
        but I got un-google-able errors about converting gunichar. This
        might be fixed in the future, in which case this function can
        be replaced with a lambda.
        """
        import string

        while (start_iter.get_char() in string.whitespace and
               start_iter.get_offset() < limit_iter.get_offset()):
            start_iter.forward_char()

    def show_lint_message(self, doc, user_data=None):
        """This signal handler is called whenever the text cursor is moved.
        It examines all the tags that apply to the cursor's position, looking
        for any that were applied by this plugin. If it finds one of our
        tags applies, it show the message in the status bar of Gedit.
        """
        cursor_pos = doc.get_property('cursor-position')

        #Examine all tags that are at the cursor position
        for tag in doc.get_iter_at_offset(cursor_pos).get_tags():
            #Look for tags that have 'pylint' in their name
            name = tag.get_property('name')
            if name is not None and 'pylint' in name:
                #Show the message in the status bar
                self.status_bar.push(self.context_id,
                                     self.lint_messages[tag]['message'])


def debug(*msg):
    """This function prints out debug messages when ENABLE_DEBUG is True.
    It is useful for tracking down errors caused by the plugin.
    """
    import sys

    if ENABLE_DEBUG:
        print('PYLINT: ', *msg, file=sys.stderr)
