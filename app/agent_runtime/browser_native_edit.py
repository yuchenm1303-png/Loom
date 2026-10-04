"""Prepare a specific editable node; Chromium owns the actual text mutation."""

from .browser_session import BrowserError


PREPARE_EDIT = r"""function(clear) {
    const el = this;
    if (!el.isConnected) throw new Error('Target element is no longer available');
    if (el.matches(':disabled') || el.readOnly) throw new Error('Target element is disabled or readonly');
    const doc = el.ownerDocument;
    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
        if (el instanceof HTMLInputElement && !['text','search','tel','url','email','password','number'].includes(el.type))
            throw new Error('Target input does not support text editing');
        el.focus({preventScroll: true});
        if (doc.activeElement !== el) throw new Error('Target element did not receive focus');
        if (clear) el.select();
    } else if (el.isContentEditable) {
        const selection = doc.getSelection();
        const saved = selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
        const inside = saved && el.contains(saved.startContainer) && el.contains(saved.endContainer);
        el.focus({preventScroll: true});
        if (doc.activeElement !== el && !el.contains(doc.activeElement)) throw new Error('Target element did not receive focus');
        const range = !clear && inside ? saved : doc.createRange();
        if (range !== saved) {
            range.selectNodeContents(el);
            if (!clear) range.collapse(false);
        }
        selection.removeAllRanges();
        selection.addRange(range);
    } else throw new Error('Target element is not editable');
    return true;
}"""


async def prepare_edit(cdp, backend_node_id: int, *, clear: bool) -> None:
    remote = await cdp.cdp_client.send.DOM.resolveNode(
        params={"backendNodeId": backend_node_id}, session_id=cdp.session_id,
    )
    object_id = remote.get("object", {}).get("objectId")
    if not object_id:
        raise BrowserError("browser type target could not be resolved")
    try:
        outcome = await cdp.cdp_client.send.Runtime.callFunctionOn(
            params={"objectId": object_id, "functionDeclaration": PREPARE_EDIT,
                    "arguments": [{"value": bool(clear)}], "returnByValue": True},
            session_id=cdp.session_id,
        )
        if outcome.get("exceptionDetails") or outcome.get("result", {}).get("value") is not True:
            # Never fall back to the focused page: that may be another field.
            description = outcome.get("result", {}).get("description", "Target preparation failed")
            raise BrowserError(str(description))
    finally:
        await cdp.cdp_client.send.Runtime.releaseObject(
            params={"objectId": object_id}, session_id=cdp.session_id,
        )
