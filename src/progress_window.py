"""
Native macOS floating progress window for Ostracon AI.
Uses AppKit (via pyobjc-framework-Cocoa) — no additional packages required.
"""

import threading
import time
import objc
from AppKit import (
    NSApplication, NSApp, NSPanel, NSTextView, NSScrollView, NSTextField,
    NSPopUpButton,
    NSColor, NSFont, NSAttributedString,
    NSForegroundColorAttributeName, NSFontAttributeName,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskUtilityWindow, NSWindowStyleMaskNonactivatingPanel,
    NSBackingStoreBuffered, NSFloatingWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSMakeRect, NSMakeSize, NSMakeRange,
    NSApplicationActivationPolicyAccessory,
    NSEvent, NSApplicationDefined,
)
from Foundation import NSObject, NSOperationQueue


# ── main-thread dispatch ──────────────────────────────────────────────────────

def _on_main(block):
    """Schedule block() on the main thread via the main NSOperationQueue."""
    NSOperationQueue.mainQueue().addOperationWithBlock_(block)


def _wake_run_loop():
    """Post a dummy event so NSApp.stop_() wakes the run loop immediately."""
    event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
        NSApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0
    )
    NSApp.postEvent_atStart_(event, True)


# ── window delegate ───────────────────────────────────────────────────────────

class _WindowDelegate(NSObject):
    """Handles window close events and the model popup's selection."""

    @objc.python_method
    def setup(self, cancel_event, on_model_change=None, on_search_change=None):
        self._cancel_event = cancel_event
        self._on_model_change = on_model_change
        self._on_search_change = on_search_change

    def windowWillClose_(self, notification):
        self._cancel_event.set()
        NSApp.stop_(None)
        _wake_run_loop()

    def modelChanged_(self, sender):
        # Fires on the main thread; hands the new value to a plain Python
        # attribute the worker thread can read without touching AppKit.
        if self._on_model_change:
            self._on_model_change(str(sender.titleOfSelectedItem()))

    def searchChanged_(self, sender):
        # The popup shows labels; the index maps back to the config value.
        if self._on_search_change:
            self._on_search_change(int(sender.indexOfSelectedItem()))


# ── public API ────────────────────────────────────────────────────────────────

class ProgressWindow:
    """
    Floating NSPanel with a scrollable log area.

    Usage::

        win = ProgressWindow(total_files=3)
        win.show()          # call before NSApp.run()
        # … launch background thread that calls win.log() / win.set_progress() …
        NSApp.run()         # blocks until finish() triggers auto-close or user closes
    """

    def __init__(self, total_files: int, models=None, current_model=None,
                 searches=None, current_search=None) -> None:
        self._total = total_files
        self._cancel_event = threading.Event()
        self._panel = None
        self._tv = None
        self._header = None
        self._delegate = None
        # Read by the worker thread between files. Switching models mid-batch
        # is cheap: prompt caches are per-model and coexist, so alternating
        # pays one write per model for the whole run, not one per switch.
        self._models = list(models or [])
        self._selected_model = current_model
        # [(config value, label)] for the type search (2b and 4b); read by the
        # worker before each file, like the model.
        self._searches = list(searches or [])
        self._selected_search = current_search
        self._build_window()

    def _set_selected_model(self, value):
        self._selected_model = value
        self.log(f"\u2699 Model switched to {value} (from the next file)", 'info')

    def _set_selected_search(self, index):
        value, label = self._searches[index]
        self._selected_search = value
        self.log(f"\u2699 Search switched to {label} (from the next file)", 'info')

    @property
    def selected_search(self):
        """The type search chosen in the popup: 'off', 'claude', 'ai-mode' or
        'gemini' (None if no popup)."""
        return self._selected_search

    @property
    def selected_model(self):
        """The model currently chosen in the popup (None if no popup)."""
        return self._selected_model

    # ── construction ─────────────────────────────────────────────────────────

    def _build_window(self):
        # Colours and fonts initialised here so NSApp is guaranteed to exist.
        self._colors = {
            'bg':      NSColor.colorWithCalibratedRed_green_blue_alpha_(0.078, 0.078, 0.090, 1.0),
            'info':    NSColor.colorWithCalibratedWhite_alpha_(0.85, 1.0),
            'success': NSColor.systemGreenColor(),
            'warning': NSColor.systemOrangeColor(),
            'error':   NSColor.systemRedColor(),
            'dim':     NSColor.colorWithCalibratedWhite_alpha_(0.40, 1.0),
            'header':  NSColor.colorWithCalibratedWhite_alpha_(0.95, 1.0),
        }
        self._font_body   = NSFont.fontWithName_size_("Menlo", 11.0) or NSFont.systemFontOfSize_(11.0)
        self._font_header = NSFont.boldSystemFontOfSize_(12.0)

        W, H = 580, 372

        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskUtilityWindow
            | NSWindowStyleMaskNonactivatingPanel
        )

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            style,
            NSBackingStoreBuffered,
            False,
        )
        panel.setReleasedWhenClosed_(False)
        panel.setTitle_("Ostracon AI — Processing")
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        panel.setBackgroundColor_(self._colors['bg'])
        panel.setOpaque_(True)
        panel.center()

        content = panel.contentView()

        # ── header label ─────────────────────────────────────────────────────
        HEADER_H = 32
        header = NSTextField.alloc().initWithFrame_(
            NSMakeRect(12, H - HEADER_H - 8, W - 24, HEADER_H)
        )
        header.setEditable_(False)
        header.setSelectable_(False)
        header.setBordered_(False)
        header.setDrawsBackground_(False)
        header.setFont_(self._font_header)
        header.setTextColor_(self._colors['header'])
        header.setStringValue_("Preparing…")
        content.addSubview_(header)

        # ── model popup ──────────────────────────────────────────────────────
        # Live, not start-only: the worker re-reads selected_model before each
        # file, so a change applies from the next item onward.
        row_y = H - HEADER_H - 36
        has_row = bool(self._models or self._searches)
        if self._models:
            label = NSTextField.alloc().initWithFrame_(NSMakeRect(12, row_y + 2, 48, 18))
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setBordered_(False)
            label.setDrawsBackground_(False)
            label.setFont_(NSFont.systemFontOfSize_(11.0))
            label.setTextColor_(self._colors['dim'])
            label.setStringValue_("Model:")
            content.addSubview_(label)

            popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(58, row_y - 2, 210, 26), False
            )
            popup.addItemsWithTitles_(self._models)
            if self._selected_model in self._models:
                popup.selectItemWithTitle_(self._selected_model)
            else:
                self._selected_model = str(popup.titleOfSelectedItem())
            popup.setFont_(NSFont.systemFontOfSize_(11.0))
            popup.setToolTip_("Extraction model; applies from the next file")
            content.addSubview_(popup)
            self._popup = popup

        # ── search popup ─────────────────────────────────────────────────────
        # Who runs the type search (2b) and its field answers (4b); "Off"
        # leaves the type to the extraction. Applies from the next file.
        self._search_popup = None
        if self._searches:
            label = NSTextField.alloc().initWithFrame_(NSMakeRect(278, row_y + 2, 52, 18))
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setBordered_(False)
            label.setDrawsBackground_(False)
            label.setFont_(NSFont.systemFontOfSize_(11.0))
            label.setTextColor_(self._colors['dim'])
            label.setStringValue_("Search:")
            content.addSubview_(label)

            spop = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(330, row_y - 2, W - 342, 26), False
            )
            spop.addItemsWithTitles_([lbl for _, lbl in self._searches])
            values = [v for v, _ in self._searches]
            if self._selected_search in values:
                spop.selectItemAtIndex_(values.index(self._selected_search))
            else:
                self._selected_search = values[int(spop.indexOfSelectedItem())]
            spop.setFont_(NSFont.systemFontOfSize_(11.0))
            spop.setToolTip_("Type search and field answers; applies from the next file")
            content.addSubview_(spop)
            self._search_popup = spop

        # ── separator line ───────────────────────────────────────────────────
        sep_y = H - HEADER_H - (48 if has_row else 20)
        sep = NSTextField.alloc().initWithFrame_(NSMakeRect(0, sep_y, W, 1))
        sep.setEditable_(False)
        sep.setBordered_(False)
        sep.setDrawsBackground_(True)
        sep.setBackgroundColor_(self._colors['dim'])
        content.addSubview_(sep)

        # ── scroll view + text view ──────────────────────────────────────────
        LOG_Y = 8
        log_h = sep_y - LOG_Y - 4
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, LOG_Y, W, log_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)

        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, W, log_h))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setDrawsBackground_(False)
        tv.setFont_(self._font_body)
        tv.setTextColor_(self._colors['info'])
        tv.setAutomaticLinkDetectionEnabled_(False)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.textContainer().setContainerSize_(NSMakeSize(W - 16, 1e7))
        tv.setHorizontallyResizable_(False)
        tv.setVerticallyResizable_(True)

        scroll.setDocumentView_(tv)
        content.addSubview_(scroll)

        # ── delegate ─────────────────────────────────────────────────────────
        delegate = _WindowDelegate.alloc().init()
        delegate.setup(self._cancel_event, self._set_selected_model, self._set_selected_search)
        panel.setDelegate_(delegate)
        if self._models:
            self._popup.setTarget_(delegate)
            self._popup.setAction_("modelChanged:")
        if self._search_popup is not None:
            self._search_popup.setTarget_(delegate)
            self._search_popup.setAction_("searchChanged:")

        self._panel    = panel
        self._tv       = tv
        self._header   = header
        self._delegate = delegate

    # ── public methods ────────────────────────────────────────────────────────

    def show(self):
        """Make the window visible. Call before NSApp.run()."""
        self._panel.orderFrontRegardless()

    def log(self, text: str, level: str = 'info') -> None:
        """Append a line to the log. Thread-safe."""
        color = self._colors.get(level, self._colors['info'])
        attr_str = NSAttributedString.alloc().initWithString_attributes_(
            text + "\n",
            {
                NSForegroundColorAttributeName: color,
                NSFontAttributeName: self._font_body,
            },
        )
        tv = self._tv

        def _append():
            storage = tv.textStorage()
            storage.beginEditing()
            storage.appendAttributedString_(attr_str)
            storage.endEditing()
            tv.scrollRangeToVisible_(NSMakeRange(storage.length(), 0))

        _on_main(_append)

    def _render_bar(self, fraction: float) -> str:
        bar_width = 18
        filled = int(bar_width * fraction)
        return '\u2588' * filled + '\u2591' * (bar_width - filled)

    def _set_header(self, text: str) -> None:
        """Write the header line. Thread-safe."""
        header = self._header

        def _update():
            header.setStringValue_(text)

        _on_main(_update)

    def set_progress(self, current: int, filename: str) -> None:
        """Update the header progress line. Thread-safe."""
        total = self._total
        fraction = (current - 1) / total if total > 0 else 0
        pct = int(fraction * 100)
        bar = self._render_bar(fraction)
        self._set_header(f"[{current}/{total}]  {filename}  {bar}  {pct}%")

    def _mark_complete(self) -> None:
        """Push the header to 100% - set_progress() only ever reflects files
        completed *before* the one currently in flight, so the last file's
        completion is never otherwise shown."""
        total = self._total
        bar = self._render_bar(1.0)
        self._set_header(f"[{total}/{total}]  Done  {bar}  100%")

    def finish(self, had_error: bool) -> None:
        """
        Signal that processing is complete. The window stays open for review
        either way - the user closes it manually - so the log is never lost
        to an auto-close before it's been read.
        """
        if not self.cancelled:
            self._mark_complete()
        if had_error:
            self.log("\u2717 Finished with errors. Close this window when done.", 'error')
        else:
            self.log("\u2713 All done. Close this window when done.", 'success')

    @property
    def cancelled(self) -> bool:
        """True if the user closed the window early."""
        return self._cancel_event.is_set()

    def make_callback(self):
        """Return a (message, level) -> None callable for use as a progress callback."""
        def _cb(message: str, level: str = 'info') -> None:
            self.log(message, level)
        return _cb


# ── standalone test ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    win = ProgressWindow(total_files=3, models=["claude-sonnet-4-6", "claude-opus-5"],
                         current_model="claude-sonnet-4-6",
                         searches=[("off", "Off"), ("claude", "Claude web search"),
                                   ("ai-mode", "Google AI Mode (Chrome)")],
                         current_search="claude")
    win.show()

    def _demo():
        files = ['Smith2024.pdf', 'Jones2023.pdf', 'Brown2022.pdf']
        for i, fname in enumerate(files, 1):
            if win.cancelled:
                break
            win.set_progress(i, fname)
            win.log(f"\U0001f4c4 Processing: {fname}", 'info')
            time.sleep(0.4)
            win.log("   Extracting text...", 'info')
            time.sleep(0.5)
            win.log("   Loading context...", 'info')
            time.sleep(0.3)
            win.log("   Sending to Claude...", 'info')
            time.sleep(0.8)
            win.log("   Validating entry...", 'info')
            time.sleep(0.2)
            win.log("   \u2713 Complete", 'success')
            time.sleep(0.2)
            win.log("   \u2713 Saved to ~/Desktop/biblio-staging.bib", 'success')
            time.sleep(0.3)

        win.finish(had_error=False)

    threading.Thread(target=_demo, daemon=True).start()
    app.run()
