import os
#from traits.etsconfig.api import ETSConfig
#ETSConfig.toolkit = 'qt4'
import numpy as np
from enable.api import ComponentEditor
from traits.api import Str, Bool, Enum, List, Dict, Any, \
    HasTraits, Instance, Button, on_trait_change
from traitsui.api import View, Group, HGroup, VGroup, HSplit, \
    Item, UItem, TreeEditor, TreeNode, Menu, Action, Handler, spring
from traitsui.key_bindings import KeyBinding, KeyBindings
from pyface.api import ImageResource, DirectoryDialog, OK, GUI, error
from fixes import fix_background_color
from chaco.api import Plot, ArrayPlotData, PlotAxis, \
    add_default_axes, add_default_grids
from chaco.tools.api import PanTool, ZoomTool
from chaco.tools.tool_states import PanState
from ui_helpers import get_file_from_dialog
import specs
import wx


# Linux/Ubuntu themes cause the background of windows to be ugly and dark
# grey. This fixes that.
fix_background_color()
APP_WIDTH = 800
title = "SinSPECt"
app_icon = os.path.join('resources','app_icon.ico')

# SPECSRegion, SPECSGroup and SpecsFile are Traited versions of SPECS xml file classes
# that represent nodes in the TreeEditor widget

class SPECSRegion(HasTraits):
    ''' The traited SPECSRegion contains a specs.SPECSRegion object and
    represents a Region node in the TreeEditor
    '''
    # This is the labeled name which is relected in the tree label
    label_name = Str('<unknown>')
    # The labeled name will be derived from this. This is also used for any matching, the
    # export filename etc.
    name = Str('<unknown>')
    region = Instance(specs.SPECSRegion)    # The reference to the contained region object
    group = Instance('SPECSGroup')          # A reference to the containing group
    selection = Instance('SelectorPanel')   # A string argument here allows a forward reference

    def __init__(self, name, region, group, **traits):
        ''' name is a string with the name of the region
        region is a specs.SPECSRegion instance
        '''
        super(SPECSRegion, self).__init__(**traits) # HasTraits.__init__(self, **traits)
        self.name = name
        self.label_name = '* {}'.format(name) # initialise label to this
        self.zero_fill_empty_channels(region)
        self.region = region
        self.group = group
        # Add a reference within the specs.SPECSRegion object in case we want access to its
        # Traited SPECSRegion owner
        self.region.owner = self
        self.selection = SelectorPanel(self)
        # Instantiation of the SelectorPanel creates it with self.selection.counts==False
        # Setting self.selection.counts=True now triggers a trait change event which
        # triggers creation of the related plot series
        # self.selection.counts = True

    def zero_fill_empty_channels(self, region):
        ''' Sometimes the underlying specs.SPECSRegion object contains None to
        indicate that no channel data is available. Here we replace any None channels
        with a zero-filled array in the underlying object
        '''
        CHANNELS = 9
        c = region.channel_counts
        if c is None:
            region.channel_counts = np.zeros((region.counts.size, CHANNELS))
        elif c.size == 0:
            # channel is empty. Just change its label for the moment
            self.label_name = '  {} (empty)'.format(self.name)
        c = region.extended_channels
        if c is None:
            region.extended_channels = np.zeros((region.counts.size, CHANNELS))

    def get_x_axis(self):
        ''' Return x-axis data based on the scan_mode metadata '''
        r = self.region
        if r.scan_mode == 'FixedAnalyzerTransmission':
            xs = r.binding_axis
        elif r.scan_mode == 'ConstantFinalState':
            xs = r.excitation_axis
        else:
            xs = r.kinetic_axis
        return xs

    def update_label(self):
        self.label_name = self._get_label()

    def _get_label(self):
        ''' Return a string with the name taken from the the underlying specs.SPECSRegion
        object prepended by an indicator of the checked status as follows:
        counts channels selected:                             *, space, name
        counts channels not selected but others are selected: -, space, name
        no     channels selected:                             space, space, name
        '''
        s = self.selection
        # see whether a region is empty
        if s.region.region.channel_counts.size == 0:
            empty_indicator = ' (empty)'
        else:
            empty_indicator = ''

        # get state of counts checkbox
        counts_state = s.get_trait_states()['counts']
        # Get sets of all the T & F channel_counts and extended_channels states,
        # i.e. end up with a set {}, {T}, {F}, or {T,F}
        channel_counts_states = s.get_channel_counts_states().values()
        if counts_state:
            if False not in channel_counts_states:
                label = '* {}{}'.format(self.name, empty_indicator)
            else:
                label = '+ {}{}'.format(self.name, empty_indicator)
        else:
            label = '  {}{}'.format(self.name, empty_indicator)
        return label


class SPECSGroup(HasTraits):
    ''' A group node in the TreeEditor '''
    name = Str('<unknown>')
    specs_regions = List(SPECSRegion)   # container for the subordinate regions


class SpecsFile(HasTraits):
    ''' The file node in the TreeEditor '''
    name = Str('<unknown>')
    specs_groups = List(SPECSGroup)     # container for the subordinate groups

    def _uniquify_names(self, names):
        ''' names is a list of strings. This generator function ensures all strings in
        the names list are unique by appending -n to the name if it is repeated, where n
        is an incrementing number. e.g. if names is ['a', 'b', 'c', 'a', 'b', 'a'] this
        yields ['a', 'b', 'c', 'a-1', 'b-1', 'a-2']
        '''
        freqs = {}
        for name in names:
            freqs[name] = freqs.get(name, 0) + 1
            if freqs[name] > 1:
                yield '{}-{}'.format(name, freqs[name]-1)
            else:
                yield name

    def open(self, filename):
        ''' Create all objects corresponding to the tree '''
        s = specs.SPECS(filename)
        self.name = filename
        group_names = [g.name for g in s.groups]
        uniquify_group_gen = self._uniquify_names(group_names)
        for group in s.groups:
            specs_group = SPECSGroup(name=uniquify_group_gen.next(), specs_regions=[])
            # Get the names from the underlying specs.SPECSRegion objects
            region_names = [r.name for r in group.regions]
            # Force them to be unique
            uniquify_region_gen = self._uniquify_names(region_names)
            # Now create our Traited SPECSRegion objects
            for region in group.regions:
                specs_group.specs_regions.append(SPECSRegion(name=uniquify_region_gen.next(),
                                                        region=region, group=specs_group))
            self.specs_groups.append(specs_group)
        return self


class TreePanel(HasTraits):
    CONTEXT_MSG = '(Set from right-click menu)'
    specs_file = Instance(SpecsFile)
    file_path = Str(None)
    most_recent_path = Str('')
    # When a selection is made this holds a list of references to selected tree nodes
    node_selection = Any()

    # Buttons in the widget group above the tree area
    bt_open_file = Button('Open file...')
    bt_export_file = Button('Export...')
    bt_copy_to_selection = Button('Paste')
    bt_clear_reference = Button('Clear')
    lb_ref = Str(CONTEXT_MSG)
    lb_norm_ref = Str(CONTEXT_MSG)
    extended_channel_ref = Enum('None', 1, 2, 3, 4, 5, 6, 7, 8, 9)('None')
    ref = Instance(SPECSRegion)
    norm_ref = Instance(SPECSRegion)
    cb_header = Bool(True)
    delimiter = Enum('tab','space','comma')('tab')

    def _bt_open_file_changed(self):
        ''' Event handler
        Called when user clicks the Open file... button
        '''
        file_path = get_file_from_dialog()
        if file_path is not None:
            self.most_recent_path = os.path.dirname(file_path)
            self.file_path = file_path

    def _file_path_changed(self, new):
        ''' Trait event handler
        When the file dialog box is closed with a file selection, open that file
        '''
        plot_panel.remove_all_plots()

        self.name = self.file_path
        try:
            GUI.set_busy()                      # set hourglass         @UndefinedVariable
            self.specs_file = SpecsFile().open(self.file_path)
        except:
            pass
        GUI.set_busy(False)                     # reset hourglass       @UndefinedVariable

    def normalise_self(self, region, ys):
        normalisation_ref = tree_panel.extended_channel_ref
        if normalisation_ref != 'None':
            # normalisation_ref is in the range 1-9
            ys /= region.region.extended_channels[:,normalisation_ref-1]
        return ys

    def normalise_double(self, region, ys, series_name, ys_column):
        # Perform double normalisation procedure on a vector of y-values (ys) that belong
        # to region (region). The column name (series_name) and column index (ys_column)
        # are also passed to enable retrieval of the correct column data from the
        # reference region.
        # The procedure is performed wrt the region selected in the tree_panel widget as
        # the reference region.
        # Double normalisation procedure is:
        # n  <- norm_ys/norm_ref_ys
        # rn <- region_ys/region_ref_ys
        # ys <- rn/n

        tnr = tree_panel.norm_ref.region
        # n  <- norm_ys/norm_ref_ys
        norm_channel = tnr.owner.selection.dbl_norm_ref
        norm_ref_ys = tnr.extended_channels[:,norm_channel-1]
        series_name_body = get_name_body(series_name)
        print series_name_body, ys_column
        if series_name == 'counts':
            norm_ys = tnr.counts
        else:
            norm_ys = tnr.__getattribute__(series_name_body)[:,ys_column-1]
        n = norm_ys / norm_ref_ys

        # rn <- region_ys/region_ref_ys
        region_ref_channel = region.selection.dbl_norm_ref
        region_ref_ys = region.region.extended_channels[:,region_ref_channel-1]
        rn = ys / region_ref_ys 

        # ys <- rn/n
        return rn / n

    def normalise(self, region, ys, series_name=None, ys_column=None):
        ys = ys.copy()
        if self.get_normalisation_mode() == 'self':
            ys = self.normalise_self(region, ys)
        else:
            # get_normalisation_mode() return value 'double'
            ys = self.normalise_double(region, ys, series_name, ys_column)
        return ys

    def _file_save(self, path):
        ''' Saves all regions set for export into a directory hierarchy rooted at path '''
        # Export all regions in all groups
        normalisation_errors = False
        for g in self.specs_file.specs_groups:
            dir_created_for_this_group = False
            for r in g.specs_regions:
                # make file region.name+'.xy' in directory g.name
                # print g.name, r.name
                # Column data contains the following in left-to-right order:
                # x-axis, counts, channel_counts_n and extended_channels_n

                if r.selection.counts:
                    # variable a holds the columnar count data. Start with the x-axis
                    # then append counts, channel_counts and extended_channels as
                    # appropriate.

                    # x-axis data
                    a = [r.get_x_axis()]

                    normalisation_ref = tree_panel.extended_channel_ref
                    normalisation_ok = True
                    try:
                        # counts data
                        ys = self.normalise(r, r.region.counts)
                        a.append(ys)
    
                        cc_dict = r.selection.get_channel_counts_states()
                        # make a string indicating the channel_counts columns summed to
                        # obtain the counts column  
                        counts_label = '+'.join([str(get_name_num(i))
                                                 for i in sorted(cc_dict) if cc_dict[i]])
                        delimiter = {'space':' ', 'comma':',', 'tab':'\t'}[self.delimiter]
    
                        # First header line
                        h = ''
                        h += '#"'
                        if normalisation_ref != 'None':
                            h += 'Normalised to:Extended channel {}, '.format(normalisation_ref)
                        h += 'Analyzer mode:{}'.format(r.region.scan_mode)
                        h += ', Dwell time:{}'.format(r.region.dwell_time)
                        h += ', Pass energy:{}'.format(r.region.pass_energy)
                        h += ', Lens mode:{}'.format(r.region.analyzer_lens)
                        if r.region.scan_mode=='FixedAnalyzerTransmission':
                            h += ', Excitation energy:{}'.format(r.region.excitation_energy)
                        elif r.region.scan_mode=='ConstantFinalState':
                            h += ', Kinetic energy:{}'.format(r.region.kinetic_energy)
                        h += '"\n'
    
                        # Second header line
                        h += '#'
                        h += {'FixedAnalyzerTransmission':'"Binding Axis"',
                              'ConstantFinalState'       :'"Excitation Axis"',
                             }.get(r.region.scan_mode,    '"Kinetic Axis"')
                        h += '{}"Counts {}"'.format(delimiter, counts_label)
    
                        # channel_counts_n data
                        for name in sorted(r.selection.get_channel_counts_states()):
                            channel_num = get_name_num(name)
                            ys = self.normalise(r, r.region.channel_counts[:,channel_num-1])
                            a.append(ys)
                            h += '{}"Channel {} counts"'.format(delimiter, channel_num)
    
                        # extended_channels_n data
                        for name in sorted(r.selection.get_extended_channels_states()):
                            channel_num = get_name_num(name)
                            ys = r.region.extended_channels[:,channel_num-1]
                            # Skipping normalisation of the extended channel used as the
                            # reference allows the normalisation to be undone from the
                            # exported data if desired. Without skipping, the reference column
                            # would be all ones and such reversion would not be possible.
                            if channel_num != normalisation_ref:
                                ys = self.normalise(r, ys)
                            a.append(ys)
                            h += '{}"Extended channel {}"'.format(delimiter, channel_num)
                    except FloatingPointError:
                        normalisation_ok = False
                        normalisation_errors = True
                        h = 'Division errors normalising to Extended channel {}'.format(
                            normalisation_ref)

                    # Write output
                    if not dir_created_for_this_group:
                        # Try creating directory if it doesn't exist
                        try:
                            dirname = os.path.join(path, g.name)
                            os.mkdir(dirname)
                            dir_created_for_this_group = True
                        except OSError:
                            # Something exists already, or it can't be written
                            # Maybe give a nice message here
                            pass

                    if normalisation_ok:
                        filename = os.path.join(dirname, r.name+'.xy')
                    else:
                        filename = os.path.join(dirname, 'ERRORS_{}.xy'.format(r.name))
                    with open(filename, 'w') as f:
                        if self.cb_header:
                            # Output header
                            print >> f, h
                        if normalisation_ok:
                            # Output data
                            a = np.array(a).transpose()
                            np.savetxt(f, a, fmt='%1.8g', delimiter=delimiter)
                        print filename, 'written'
        if normalisation_errors:
            error(None, h)

    def _bt_export_file_changed(self):
        ''' Event handler
        Called when the user clicks the Export... button
        '''
        myFrame = wx.GetApp().GetTopWindow()
        dlg = DirectoryDialog(default_path=self.most_recent_path, parent=myFrame, style='modal')
        if dlg.open() == OK:
            self.most_recent_path = dlg.path
            self._file_save(dlg.path)

    def _has_data(self):
        return self.file_path is not None

    def _reference_set(self):
        return self.lb_ref != self.CONTEXT_MSG

    def _norm_reference_set(self):
        return self.lb_norm_ref != self.CONTEXT_MSG

    def get_normalisation_mode(self):
        ''' Returns the current normalisation mode: one of 'self' or 'double'
        '''
        if self._norm_reference_set():
            return 'double'
        else:
            return 'self'

    def _group_select(self):
        # print 'gs', self.name
        pass

    def _bt_copy_to_selection_changed(self):
        if self.ref is not None:
            trait_dict = self.ref.selection.get_trait_states()
            for r in tree_panel.node_selection:
                if isinstance(r, SPECSRegion):
                    # paste all counts, channel_counts_ and extended_channels_ states
                    r.selection.set(**trait_dict)

    def _extended_channel_ref_changed(self):
        ''' If the normalisation channel drop-down selection is changed, force a refresh
        of the plots in the current selection to ensure that they all have the latest
        normalisation applied '''
        if isinstance(self.node_selection[0], SPECSRegion):
            self.node_selection[0].selection._refresh_current_view()

    def _bt_clear_reference_changed(self):
        self.lb_norm_ref = self.CONTEXT_MSG
        self.norm_ref = None
        if isinstance(self.node_selection[0], SPECSRegion):
            self.node_selection[0].selection.refresh_dbl_norm_ref()

    def _region_select(self):
        # Update SelectorPanel
        main_app.selector_panel = self.selection

        # Remove current plots from the plot window foreground layer
        plot_panel.remove_all_plots(draw_layer='foreground')
        # Add any checked counts and channels to the plot window foreground layer
        self.selection.plot_checkbox_states()

    def _group_dclick(self):
        '''
        Double-clicking a node cycles through selection states of subordinate regions
        all-on -> last-selection -> all-off -> all-on -> ...
        '''
        try:
            GUI.set_busy()                      # set hourglass         @UndefinedVariable
            region_state = {r.selection.counts for r in self.specs_regions}
            if True in region_state:
                # at least one of the regions is enabled, disable all
                for r in self.specs_regions:
                    r.selection.region_cycle(all_off=True)
            else:
                # enable all counts
                for r in self.specs_regions:
                    r.selection.region_cycle(counts_only=True)
        except:
            pass
        GUI.set_busy(False)                     # reset hourglass       @UndefinedVariable

    def _region_dclick(self):
        '''
        Double-clicking a node cycles through selection states of subordinate channels
        all-on -> last-selection -> all-off -> all-on -> ...
        '''
        for s in tree_panel.node_selection:
            s.icon = 'none'
        tree_panel._cycle_region_key()

    def _cycle_region_key(self, info=None):
        try:
            GUI.set_busy()                      # set hourglass         @UndefinedVariable
            for n in self.node_selection:
                if isinstance(n, SPECSRegion):
                    n.selection.region_cycle()
                elif isinstance(n, SPECSGroup):
                    for r in n.specs_regions:
                        r.selection.region_cycle()
        except:
            pass
        GUI.set_busy(False)                     # reset hourglass       @UndefinedVariable

    def _change_selection_state(self, selection, set_state='toggle'):
        try:
            GUI.set_busy()                      # set hourglass         @UndefinedVariable
            for n in selection:
                if isinstance(n, SPECSRegion):
                    n.selection.counts = \
                        not n.selection.counts if set_state=='toggle' else set_state
                elif isinstance(n, SPECSGroup):
                    for r in n.specs_regions:
                        r.selection.counts = \
                            not r.selection.counts if set_state=='toggle' else set_state
        except:
            pass
        GUI.set_busy(False)                     # reset hourglass       @UndefinedVariable

    def _toggle_key(self, info):
        # toggle counts of selection
        self._change_selection_state(self.node_selection)

    def _select_key(self, info):
        # set counts of selection True
        self._change_selection_state(self.node_selection, set_state=True)

    def _deselect_key(self, info):
        # set counts of selection False
        self._change_selection_state(self.node_selection, set_state=False)

    class TreeHandler(Handler):
        ''' This Handler supports the right-click menu actions '''
        def _menu_set_as_reference(self, editor, obj):
            ''' Sets the current tree node object as the source for copying state to
            selected tree items.
            '''
            tree_panel.ref = obj
            tree_panel.lb_ref = obj.name

        def _menu_set_as_norm_reference(self, editor, obj):
            ''' Sets the current tree node object as the source for normalisation. '''
            tree_panel.norm_ref = obj
            tree_panel.lb_norm_ref = obj.name
            # Now refresh the selection panel to force its drop-down selector to appear
            tree_panel.node_selection[0].selection.refresh_dbl_norm_ref()


    # View for objects that aren't edited
    no_view = View()

    # Tree editor
    tree_editor = TreeEditor(
        nodes = [
            TreeNode( node_for  = [SpecsFile],
                      auto_open = True,
                      children  = 'specs_groups',
                      label     = 'name',
                      view      = no_view,
                      add       = [SPECSGroup],
                      menu      = Menu(),
                      #on_dclick = _bt_open_file_changed,
                      rename_me = False,
                    ),

            TreeNode( node_for  = [SPECSGroup],
                      auto_open = True,
                      children  = 'specs_regions',
                      label     = 'name',
                      view      = no_view,
                      add       = [SPECSRegion],
                      menu      = Menu(),
                      rename_me = False,
                      on_select = _group_select,
                      on_dclick = _group_dclick,
                    ),

            TreeNode( node_for  = [SPECSRegion],
                      auto_open = True,
                      label     = 'label_name',
                      view      = no_view,
                      menu      = Menu(
                                    Action(name='Set selection region',
                                           action='handler._menu_set_as_reference(editor,object)'),
                                    Action(name='Set normalisation region',
                                           action='handler._menu_set_as_norm_reference(editor,object)'),
                                  ),
                      rename_me = False,
                      on_select = _region_select,
                      on_dclick = _region_dclick,
                    )
        ],
        editable = False,           # suppress the editor pane as we are using the separate Chaco pane for this
        selected = 'node_selection',
        selection_mode = 'extended',
    )

    key_bindings = KeyBindings(
        KeyBinding( binding1    = 'Space',
                    binding2    = 't',
                    description = 'Toggle Selection',
                    method_name = '_toggle_key' ),
        KeyBinding( binding1    = '+',
                    binding2    = '=',
                    description = 'Select',
                    method_name = '_select_key' ),
        KeyBinding( binding1    = '-',
                    description = 'Deselect',
                    method_name = '_deselect_key' ),
        KeyBinding( binding1    = 'c',
                    description = 'Cycle region',
                    method_name = '_cycle_region_key' ),
    )

    # The tree view
    traits_view =   View(
                        UItem('bt_open_file'),
                        VGroup(
                            HGroup(
                                Item('lb_ref', label='Region', style='readonly'),
                                spring,
                                UItem('bt_copy_to_selection', enabled_when='object._reference_set()'),
                            ),
                            label = 'Selection',
                            show_border = True,
                        ),
                        VGroup(
                            HGroup(
                                Item('lb_norm_ref', label='Region', style='readonly'),
                                spring,
                                Item('extended_channel_ref', label='ref:', enabled_when='object._has_data() and not object._norm_reference_set()')
                            ),
                            UItem('bt_clear_reference', visible_when='object._norm_reference_set()'),
                            label = 'Normalisation',
                            show_border = True,
                        ),
                        UItem(
                            name = 'specs_file',
                            editor = tree_editor,
                        ),
                        VGroup(
                            HGroup(
                                Item('cb_header', label='Include header'),
                                Item('delimiter'),
                            ),
                            UItem('bt_export_file'),
                            enabled_when = 'object._has_data()',
                            label = 'Data Export',
                            show_border = True,
                        ),
                        key_bindings = key_bindings,
                        handler = TreeHandler(),
                    )


class PanToolWithHistory(PanTool):
    def __init__(self, *args, **kwargs):
        self.history_tool = kwargs.get('history_tool', None)
        if 'history_tool' in kwargs:
            del kwargs['history_tool']
        super(PanToolWithHistory, self).__init__(*args, **kwargs)

    def _start_pan(self, event, capture_mouse=False):
        super(PanToolWithHistory, self)._start_pan(event, capture_mouse=False)
        if self.history_tool is not None:
            self._start_pan_xy = self._original_xy
            # Save the current data range center so this movement can be
            # undone later.
            self._prev_state = self.history_tool.data_range_center()

    def _end_pan(self, event):
        super(PanToolWithHistory, self)._end_pan(event)
        if self.history_tool is not None:
            # Only append to the undo history if we have moved a significant amount. 
            # This avoids conflicts with the single-click undo function.
            new_xy = np.array((event.x, event.y))
            old_xy = np.array(self._start_pan_xy)
            if any(abs(new_xy - old_xy) > 10):
                current = self.history_tool.data_range_center()
                prev = self._prev_state
                if current != prev:
                    self.history_tool.append_state(PanState(prev, current))


class ClickUndoZoomTool(ZoomTool):
    def __init__(self, component=None, undo_button='right', *args, **kwargs):
        super(ClickUndoZoomTool, self).__init__(component, *args, **kwargs)
        self.undo_button = undo_button
        self._reverting = False
        self.minimum_undo_delta = 3

    def normal_left_down(self, event):
        """ Handles the left mouse button being pressed while the tool is
        in the 'normal' state.

        If the tool is enabled or always on, it starts selecting.
        """
        if self.undo_button == 'left':
            self._undo_screen_start = (event.x, event.y)
        super(ClickUndoZoomTool, self).normal_left_down(event)

    def normal_right_down(self, event):
        """ Handles the right mouse button being pressed while the tool is
        in the 'normal' state.

        If the tool is enabled or always on, it starts selecting.
        """
        if self.undo_button == 'right':
            self._undo_screen_start = (event.x, event.y)
        super(ClickUndoZoomTool, self).normal_right_down(event)

    def normal_left_up(self, event):
        if self.undo_button == 'left':
            if self._mouse_didnt_move(event):
                self.revert_history()

    def normal_right_up(self, event):
        if self.undo_button == 'right':
            if self._mouse_didnt_move(event):
                self.revert_history()

    def selecting_left_up(self, event):
        self.normal_left_up(event)
        super(ClickUndoZoomTool, self).selecting_left_up(event)

    def selecting_right_up(self, event):
        self.normal_right_up(event)
        super(ClickUndoZoomTool, self).selecting_right_up(event)

    def _mouse_didnt_move(self, event):
        start = np.array(self._undo_screen_start)
        end = np.array((event.x, event.y))
        return all(abs(end - start) == 0)

    def clear_undo_history(self):
        self._history_index = 0
        self._history = self._history[:1]

    def revert_history(self):
        if self._history_index > 0:
            self._history_index -= 1
            self._prev_state_pressed()

    def revert_history_all(self):
        self._history_index = 0
        self._reset_state_pressed()

    def _get_mapper_center(self, mapper):
        bounds = mapper.range.low, mapper.range.high
        return bounds[0] + (bounds[1] - bounds[0])/2.

    def data_range_center(self):
        x_center = self._get_mapper_center(self._get_x_mapper())
        y_center = self._get_mapper_center(self._get_y_mapper())
        return x_center, y_center

    def append_state(self, state):
        self._append_state(state, set_index=True)


class PlotPanel(HasTraits):
    ''' The Chaco plot area.
    '''
    plot_data = Instance(ArrayPlotData)
    plot = Instance(Plot)
    LAYERS = ['background', 'foreground', 'highlight']

    def __init__(self, **traits):
        super(PlotPanel, self).__init__(**traits)   # HasTraits.__init__(self, **traits)
        self.plot_data = ArrayPlotData()
        self.plot = Plot(self.plot_data)

        # Extend the plot panel's list of drawing layers
        ndx = self.plot.draw_order.index('plot')
        self.plot.draw_order[ndx:ndx] = self.LAYERS

        self.plot.value_range.low = 0               # fix y-axis min to 0
        self.plot.index_axis = PlotAxis(self.plot, orientation='bottom', title='Energy [eV]')
        self.plot.y_axis = PlotAxis(self.plot, orientation='left', title='Intensity [Counts]')

        # Now add a transparent 1st plot series that never gets removed
        # since if we remove the first instance via remove() the mapper and tools are also removed
        plot = self.add_plot('tool_plot', [0,1], [0,1], bgcolor='white', color='transparent')
        self.value_mapper, self.index_mapper = self._setup_plot_tools(plot)

    def add_plot(self, name, xs, ys, draw_layer='foreground', **lineplot_args):
        name = '_'.join([draw_layer, name])
        self.plot_data.set_data(name+'_xs', xs)
        self.plot_data.set_data(name+'_ys', ys)
        renderer = self.plot.plot((name+'_xs', name+'_ys'), name=name, type='line', **lineplot_args)[0]
        renderer.set(draw_layer=draw_layer)

        self.plot.request_redraw()
        return self.plot

    def remove_plot(self, name, draw_layer='foreground'):
        if name == 'tool_plot':
            # Never remove this one since the chaco tools are attached to it 
            return
        if name.split('_')[0] not in self.LAYERS:
            name = '_'.join([draw_layer, name])
        try:
            self.plot_data.del_data(name+'_xs')
            self.plot_data.del_data(name+'_ys')
            self.plot.delplot(name)
            # Invalidate the datasources as chaco maintains a cache:
            # See http://thread.gmane.org/gmane.comp.python.chaco.user/658/focus=656
            self.plot.datasources.clear()
        except KeyError:
            pass
        self.plot.request_redraw()

    def update_plot_data(self, name, data, x_or_y='y', draw_layer='foreground', **lineplot_args):
        name = '_'.join([draw_layer, name])
        xy_suffix = {'x':'_xs', 'y':'_ys'}[x_or_y]
        self.plot_data.set_data(name+xy_suffix, data)
        self.plot.request_redraw()

    def remove_all_plots(self, draw_layer=None):
        ''' When draw_layer is None, removes all plots from all layers (except 'tool_plot'
        which is never removed). Otherwise, removes all plots from the specified
        draw_layer which is assumed to one of the values in the LAYERS list.
        '''
        for p in plot_panel.plot.plots.keys():
            if (draw_layer is None) or (p.split('_')[0] == draw_layer):
                plot_panel.remove_plot(p)

    def get_plot(self, name, draw_layer=None):
        ''' Get a plot reference from the name '''
        if draw_layer is not None:
            name = '_'.join([draw_layer, name])
        return self.plot.plots[name][0]

    def set_plot_attributes(self, name, draw_layer='foreground', **attributes):
        name = '_'.join([draw_layer, name])
        try:
            for key, value in attributes.iteritems():
                setattr(self.plot.plots[name][0], key, value)
        except KeyError:
            pass

    def _setup_plot_tools(self, plot):
        ''' Sets up the background, and several tools on a plot '''
        # Make a white background with grids and axes
        plot.bgcolor='white'
        add_default_grids(plot)
        add_default_axes(plot)

        # The PanTool allows panning around the plot
        plot.tools.append(PanToolWithHistory(plot, drag_button='right'))

        # The ZoomTool tool is stateful and allows drawing a zoom
        # box to select a zoom region.
        zoom = ClickUndoZoomTool(plot, tool_mode="box", always_on=True)
        plot.overlays.append(zoom)

        return plot.value_mapper, plot.index_mapper

    traits_view =   View(
                        UItem(
                            'plot',
                            editor=ComponentEditor(),
                            show_label=False
                        ),
                        resizable=True,
                    )


def get_name_body(series_name):
    ''' Get first part of name
    e.g. get_name_body('foo_bar_baz') returns foo_bar
    '''
    return '_'.join(series_name.split('_')[:2])

def get_name_num(series_name):
    ''' Get last part of name.
    e.g. get_name_num('foo_bar_baz') returns baz
    '''
    return int(series_name.split('_')[-1])

def is_bool_trait(obj, t):
    ''' Return true iff t evaluates to a bool when accessed as an attribute,
    such as a Bool trait 
    '''
    try:
        return type(obj.__getattribute__(t)) is bool
    except AttributeError:
        return False


class SelectorPanel(HasTraits):
    '''
    A panel of checkboxes reflecting the channels within the specs region used for
    toggling plot visibility and flagging for export.
    A "better" way to implement this would be to create a "dynamic view" that has a
    checkbox for each Bool trait in the region object. However, dynamic views seem
    extremely tricky, so instead I just reflect the state of the region object's traits
    in the checkboxes here.
    See TraitsUI documentation Example 5b
    http://docs.enthought.com/traitsui/traitsui_user_manual/advanced_view.html
    Instead of just defining a default view here, I build it using
    def default_traits_view(self):
    '''
    global tree_panel
    global selector_panel
    global plot_panel

    name = Str('<unknown>')
    region = Instance(SPECSRegion)
    last_selection = Dict   # stores counts and channel traits whenever a checkbox is clicked
    cycle_state = Enum('counts_on', 'channels_on', 'all_on')
    cycle_channel_counts_state = Enum('all_on', 'all_off')('all_on')
    cycle_extended_channels_state = Enum('all_on', 'all_off')('all_off')
    bt_cycle_channel_counts = Button('All on/off')
    bt_cycle_extended_channels = Button('All on/off')
    dbl_norm_ref = Enum(1, 2, 3, 4, 5, 6, 7, 8, 9)(3)
    toggle_to_force_refresh = Bool(False)   # Used by the refresh_dbl_norm_ref() method 

    # error = Property(Bool, sync_to_view='counts.invalid')

    def __init__(self, region=None, **traits):
        super(SelectorPanel, self).__init__(**traits)   # HasTraits.__init__(self, **traits)
        if region is None:      # This will be the case on the first call
            return
        self.region = region

        # create a trait for the counts checkbox
        self.add_trait('counts', Bool(True))

        # create traits for each channel_counts_n checkbox
        if region.region.channel_counts is not None:
            channel_counts_len = region.region.channel_counts.shape[1]
            for i in range(channel_counts_len):
                self.add_trait('channel_counts_{}'.format(i+1), Bool(True))
            # use self._instance_traits() to list these traits

        # create traits for each extended_channels_n checkbox
        if region.region.extended_channels is not None:
            extended_channels_len = region.region.extended_channels.shape[1]
            for i in range(extended_channels_len):
                self.add_trait('extended_channels_{}'.format(i+1), Bool)
        # Now we've created all the Bool/checkbox traits default_traits_view() can
        # create a view for them.

        self.cycle_state = 'counts_on'

    def _norm_reference_set(self):
        return tree_panel.lb_norm_ref != tree_panel.CONTEXT_MSG

    def default_traits_view(self):
        '''
        Called to create the selection view to be shown in the selection panel.
        This is also the default View called when the GUI is first initialised with a
        "None" SelectorPanel.
        https://mail.enthought.com/pipermail/enthought-dev/2012-May/031008.html
        '''
        items = []
        if 'counts' in self._instance_traits():
            group1 = HGroup()
            group1.content = []

            # counts group
            group = HGroup()
            group.content = []
            group.content.append(Item('counts', label='Counts'))
            group.show_border = True
            group1.content.append(group)

            # channel_counts_x group
            channel_counts_buttons = [Item(name, label=str(get_name_num(name)))
                            for name in sorted(self.get_channel_counts_states())]
            channel_counts_buttons.append(UItem('bt_cycle_channel_counts'))
            if len(channel_counts_buttons) > 0:
                group = HGroup()
                group.content = channel_counts_buttons
                group.show_border = True
                group.label = 'Channel Counts'
                group1.content.append(group)

            # extended_channels_x group
            extended_channels_buttons = [Item(name, label=str(get_name_num(name)))
                            for name in sorted(self.get_extended_channels_states())]
            extended_channels_buttons.append(UItem('bt_cycle_extended_channels'))
            if len(extended_channels_buttons) > 0:
                group = HGroup()
                group.content = extended_channels_buttons
                group.show_border = True
                group.label = 'Extended Channels'
                group1.content.append(group)

                # extended channel double normalisation reference
                group = HGroup()
                group.content = [Item('dbl_norm_ref', label='ref:')]
                group.show_border = True
                group.label = 'Dbl nrm ref'
                group.visible_when = 'object._norm_reference_set()'
                group1.content.append(group)

            group1.label = self.region.name
            group1.show_border = True
            items.append(group1)
        return View(*items)

    def refresh_dbl_norm_ref(self):
        ''' Forces the traitsui visible_when conditions to be checked.
        According to the traitsui docs, "all visible_when conditions are checked each time
        that any trait value is edited in the display." It turns out that toggling this
        trait forces the visible_when conditions to be checked despite the trait not
        having a corresponding Item in the selection panel View.
        '''
        self.toggle_to_force_refresh = not self.toggle_to_force_refresh

    def _counts_changed(self, trait, old, new):
        ''' Trait event handler
        The counts checkbox was toggled
        '''
        if new:
            self._add_plot(self.region, trait)
        else:
            self._remove_plot(self.region, trait)
        self.region.update_label()

    def refresh_counts(self):
        ''' Refresh counts computation and plot series by toggling one of the
        channel_counts_ series to cause a _channel_counts_x_changed() trait change event.
        '''
        self.channel_counts_1 = not self.channel_counts_1 
        self.channel_counts_1 = not self.channel_counts_1 

    @on_trait_change('channel_counts_+')
    def _channel_counts_x_changed(self, container, trait, new):
        ''' Trait event handler
        A channel_counts_n checkbox was toggled
        '''
        # add or remove the channel counts plot from screen
        if new:
            self._add_plot(self.region, trait, get_name_num(trait))
        else:
            self._remove_plot(self.region, trait)

        # recompute counts
        # channel_counts is an n-column (n=9) x m-row array
        # make a mask corresponding to the checkbox state then sum the corresponding columns
        cc_dict = self.get_channel_counts_states()
        mask = np.array([cc_dict[i] for i in sorted(cc_dict)])
        self.region.region.counts = self.region.region.channel_counts[:,mask].sum(axis=1)

        # update the counts series plot data
        # This is done by toggling the counts plot off and on again if it is currently on
        # I tried this by directly updating the counts y-data which should trigger a chaco
        # update, but there seems to be a bug in chaco that makes updating the 
        # series flaky - it doesn't always update the plot. This way works by triggering
        # the _counts_changed() event
        if self.counts:
            self.counts = False
            self.counts = True

        # update the tree label to indicate the selection
        self.region.update_label()

    @on_trait_change('extended_channels_+')
    def _extended_channels_x_changed(self, container, trait, new):
        ''' Trait event handler
        An extended_channels_n checkbox was toggled
        '''
        if new:
            self._add_plot(self.region, trait, get_name_num(trait))
        else:
            self._remove_plot(self.region, trait)
        self.region.update_label()

    def _dbl_norm_ref_changed(self):
        self._refresh_current_view()

    def _name_plot(self, region, series_name):
        ''' Make a unique name based on the group_name, region_name and series_name parts
        which together form a unique triple because we enforced uniqueness when reading
        the data
        '''
        return '_'.join([region.group.name, region.name, series_name])

    def _add_plot(self, region, series_name, column=None):
        ''' Adds a plot to the chaco plot widget. '''
        name = self._name_plot(region, series_name)
        xs = self.region.get_x_axis()
        if series_name == 'counts':
            series_name_body = 'counts'
            ys = self.region.region.counts
        else:
            # Get 'first_second' part of the name 'first_second_n' which will either be
            # 'channel_counts' or 'extended_channels'. Then use this to retrieve the
            # matching array from the specs.SPECSRegion object.
            series_name_body = get_name_body(series_name)
            ys = self.region.region.__getattribute__(series_name_body)[:,column-1]
        ys = ys.copy()      # deepcopy in anticipation of having to renormalise the data

        # If normalising, rescale here, which takes care of rendering correctly. This
        # strategy requires normalisation to be performed again at the export stage.
        try:
            ys = tree_panel.normalise(self.region, ys, series_name, column)
        except (FloatingPointError, ValueError):
            ys = np.zeros_like(ys)  # do this so display reflects that normalisation failed

        line_attributes = { \
            'counts'            : {'color':'black', 'width':2.0},
            'channel_counts'    : {'color':'blue' , 'width':1.5},
            'extended_channels' : {'color':'red'  , 'width':1.5},
            }[series_name_body]
        plot_panel.add_plot(name, xs, ys, **line_attributes)

    def _remove_plot(self, region, series_name):
        ''' Call plot widget to remove it and delete the reference here. '''
        name = self._name_plot(region, series_name)
        plot_panel.remove_plot(name)

    def _refresh_current_view(self):
        ''' Toggle off and on all boolean/checkbox traits in the current selection.
        This is intended to be called when the normalisation reference channel is updated
        to force replotting and recalculation of the data. '''
        trait_dict = self.get_trait_states()
        true_traits = [i for i in trait_dict if trait_dict[i]]
        params_to_reset_traits = {i:False for i in true_traits}
        params_to_set_traits = {i:True for i in true_traits}
        # toggle off then on
        self.set(**params_to_reset_traits)
        self.set(**params_to_set_traits)

    def plot_checkbox_states(self):
        ''' Add plots to the default (foreground) layer reflecting the checkbox states
        '''
        trait_dict = self.get_trait_states()
        for trait, val in trait_dict.iteritems():
            if val:
                if trait=='counts':
                    self._add_plot(self.region, trait)
                else:
                    # channel_counts_+, extended_channels_+
                    self._add_plot(self.region, trait, get_name_num(trait))

    def get_trait_states(self):
        ''' Return a dictionary of all trait_name:value entries with associated
        checkboxes in this selector panel.
        '''
        # I don't know how to evaluate the trait values directly from _instance_traits()
        # so I use it to get the names then use __getattribute__() to evaluate them.
        trait_dict = {i: self.__getattribute__(i)
                      for i in self._instance_traits()
                      if is_bool_trait(self, i)}
        return trait_dict

    def get_channel_counts_states(self):
        ''' Return a dictionary of trait_name:value entries associated with the
        channel_counts_ checkboxes in the selector panel.
        '''
        trait_dict = {i: self.__getattribute__(i)
                      for i in self._instance_traits()
                      if get_name_body(i)=='channel_counts'}
        return trait_dict

    def get_extended_channels_states(self):
        ''' Return a dictionary of trait_name:value entries associated with the
        extended_channels_ checkboxes in the selector panel.
        '''
        trait_dict = {i: self.__getattribute__(i)
                      for i in self._instance_traits()
                      if get_name_body(i)=='extended_channels'}
        return trait_dict

    def region_cycle(self, all_off=False, counts_only=False):
        ''' Cycle the state of the selected channels
        '''
        if all_off:
            self.trait_set(**{i: False for i in self._instance_traits()
                              if is_bool_trait(self, i)})
            self.trait_set(**{i: True for i in self._instance_traits()
                              if get_name_body(i)=='channel_counts'})
            self.counts = False
            self.cycle_state = 'channels_on'
            return

        if counts_only:
            self.trait_set(**{i: True for i in self._instance_traits()
                              if get_name_body(i)=='channel_counts'})
            self.counts = True
            self.cycle_state = 'counts_on'
            return

        if self.cycle_state == 'counts_on':
            self.trait_set(**{i: True for i in self._instance_traits()
                              if get_name_body(i)=='channel_counts'})
            self.counts = False
            self.cycle_state = 'channels_on'
        elif self.cycle_state == 'channels_on':
            self.trait_set(**{i: True for i in self._instance_traits()
                              if is_bool_trait(self, i)})
            self.counts = True
            self.cycle_state = 'all_on'
        elif self.cycle_state == 'all_on':
            self.trait_set(**{i: False for i in self._instance_traits()
                              if is_bool_trait(self, i)})
            self.trait_set(**{i: True for i in self._instance_traits()
                              if get_name_body(i)=='channel_counts'})
            self.counts = True
            self.cycle_state = 'counts_on'

    def _bt_cycle_channel_counts_changed(self):
        ''' Toggle the state of the counts channels
        '''
        channel_counts_states = self.get_channel_counts_states().values()
        if (False in channel_counts_states) and (True in channel_counts_states):
            self.cycle_channel_counts_state = 'all_off'

        if self.cycle_channel_counts_state == 'all_on':
            self.trait_set(**{i: False for i in self._instance_traits()
                              if get_name_body(i)=='channel_counts'})
            self.cycle_channel_counts_state = 'all_off'
        else:
            self.trait_set(**{i: True for i in self._instance_traits()
                              if get_name_body(i)=='channel_counts'})
            self.cycle_channel_counts_state = 'all_on'

    def _bt_cycle_extended_channels_changed(self):
        ''' Toggle the state of the counts channels
        '''
        extended_channels_states = self.get_extended_channels_states().values()
        if (False in extended_channels_states) and (True in extended_channels_states):
            self.cycle_extended_channels_state = 'all_off'

        if self.cycle_extended_channels_state == 'all_on':
            self.trait_set(**{i: False for i in self._instance_traits()
                              if get_name_body(i)=='extended_channels'})
            self.cycle_extended_channels_state = 'all_off'
        else:
            self.trait_set(**{i: True for i in self._instance_traits()
                              if get_name_body(i)=='extended_channels'})
            self.cycle_extended_channels_state = 'all_on'


class MainApp(HasTraits):
    # Left Panel
    tree_panel = Instance(TreePanel)
    selector_panel = Instance(SelectorPanel)
    # Right Panel
    plot_panel = Instance(PlotPanel)

    # The main view
    traits_view =   View(
                        HSplit(
                            Group(
                                UItem('tree_panel', style='custom', width=APP_WIDTH*0.2),
                            ),
                            Group(
                                VGroup(
                                    UItem('selector_panel', style='custom', width=APP_WIDTH*.8),
                                    UItem('plot_panel', style='custom'),
                                ),
                            ),
                        ),
                    title = title,
                    icon = ImageResource(app_icon),
                    id = 'app.main_view',
                    dock = 'horizontal',
                    drop_class = HasTraits,
                    resizable = True,
                    )


_info_html = \
"""
<h5>Plot region usage</h5>
Left drag = Zoom a selection of the plot <br>
Right drag = Pan the plot <br>
Right click = Undo zoom <br>
Esc = Reset zoom/pan <br>
Mousewheel = Zoom in/out <br>

<h5>Keyboard shortcuts for tree selections</h5>
+,=      Select
-        Deselect
Space,t  Toggle counts
c        Cycle

<h5>About the software</h5>

Please send bug reports and suggestions to <br>
sinspect@synchrotron.org.au <br>

Software authors: <br>
Gary Ruben, Victorian eResearch Strategic Initiative (VeRSI), gruben@versi.edu.au <br>
Kane O'Donnell, Australian Synchrotron <br>
http://www.versi.edu.au <br>

Software home: <br>
http://www.synchrotron.org.au/sinspect <br>
Software source: <br>
http://github.com/AustralianSynchrotron/sinspect <br>

Recognition of NeCTAR funding: <br>
The Australian Synchrotron is proud to be in partnership with the National eResearch Collaboration Tools and
Resources (NeCTAR) project to develop eResearch Tools for the synchrotron research community. This will enable our
scientific users to have instant access to the results of data during the course of their experiment which will
facilitate better decision making and also provide the opportunity for ongoing data analysis via remote access.

Copyright (c) 2013, Australian Synchrotron Company Ltd <br>
All rights reserved.
"""


if __name__ == "__main__":
    np.seterr(divide='raise')
    tree_panel = TreePanel(specs_file=SpecsFile())
    selector_panel = SelectorPanel()
    plot_panel = PlotPanel()
    main_app = MainApp(
        tree_panel=tree_panel,
        selector_panel=selector_panel,
        plot_panel=plot_panel,
        )
    main_app.configure_traits()
