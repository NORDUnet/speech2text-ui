from nicegui import ui
from nicegui.testing.user_simulation import user_simulation
import pytest

from utils.background_upload import show_upload_failure
from utils.upload_state import Upload


@pytest.mark.asyncio
async def test_failure_dialog_shows_reason_until_dismissed():
    clients = []
    async with user_simulation() as user:
        @ui.page('/test-upload-failure')
        def page():
            clients.append(ui.context.client)
            ui.button('Show failure', on_click=lambda: show_upload_failure(
                Upload('meeting.mp4', 1, phase='Failed', error='Shared monthly quota exceeded.')
            ))
        await user.open('/test-upload-failure')
        user.find('Show failure').click()
        await user.should_see('Transcription could not start')
        await user.should_see('meeting.mp4')
        await user.should_see('Shared monthly quota exceeded.')
        dialog = next(element for element in clients[0].elements.values() if isinstance(element, ui.dialog))
        assert dialog.value is True
        user.find('Close').click()
        assert dialog.value is False
