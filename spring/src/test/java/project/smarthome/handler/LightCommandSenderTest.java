package project.smarthome.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import project.smarthome.control.ButtonEventId;
import project.smarthome.control.ControlService;
import project.smarthome.control.LightControl;

import java.net.InetSocketAddress;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.after;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class LightCommandSenderTest {
    @Test
    void replacementNeedsAClosedOldSessionAndMatchingState() throws Exception {
        var mapper = new ObjectMapper();
        var control = new ControlService(2);
        var sessions = new DeviceSessions();
        var old = mock(WebSocketSession.class);
        var replacement = mock(WebSocketSession.class);
        var address = new InetSocketAddress("127.0.0.1", 9000);
        when(old.getRemoteAddress()).thenReturn(address);
        when(replacement.getRemoteAddress()).thenReturn(address);
        when(old.isOpen()).thenReturn(true);
        when(replacement.isOpen()).thenReturn(true);
        var sender = new LightCommandSender(mapper, sessions, Optional.of(control), "127.0.0.1", 0);
        try {
            sessions.register(old);
            assertEquals("INITIALIZED", sender.initialize(old, "light-run", true));
            sessions.register(replacement);
            assertEquals("ALREADY_INITIALIZED", sender.initialize(replacement, "light-run", true));
            when(old.isOpen()).thenReturn(false);
            assertEquals("STATE_MISMATCH", sender.initialize(replacement, "light-run", false));
            assertEquals("RECONNECTED", sender.initialize(replacement, "light-run", true));

            sessions.unregister(old);
            sender.disconnected(old);
            assertEquals(LightControl.Admission.ACCEPTED,
                    sender.acceptButtonPress(new ButtonEventId("mainpi", "run", 1)));
            sender.dispatch();
            verify(replacement, timeout(1000)).sendMessage(any());
        } finally {
            sender.stop();
        }
    }

    @ParameterizedTest
    @CsvSource({"mainpi/run/2,true", "mainpi/run/1,false"})
    void mismatchingSnapshotKeepsTheWaitingCommands(String lastApplied, boolean on) throws Exception {
        var mapper = new ObjectMapper();
        var control = new ControlService(2);
        var sessions = new DeviceSessions();
        var light = mock(WebSocketSession.class);
        when(light.getRemoteAddress()).thenReturn(new InetSocketAddress("127.0.0.1", 9000));
        when(light.isOpen()).thenReturn(true);
        sessions.register(light);
        var sender = new LightCommandSender(mapper, sessions, Optional.of(control), "127.0.0.1", 20);
        var frames = new LinkedBlockingQueue<TextMessage>();
        try {
            doAnswer(call -> {
                frames.add(call.getArgument(0));
                return null;
            }).when(light).sendMessage(any());
            assertEquals("INITIALIZED", sender.initialize(light, "light-run", false));
            sender.acceptButtonPress(new ButtonEventId("mainpi", "run", 1));
            sender.acceptButtonPress(new ButtonEventId("mainpi", "run", 2));
            assertFalse(sender.requestStateQuery());
            sender.dispatch();
            await().until(sender::requestStateQuery);
            assertFalse(sender.requestStateQuery());
            assertFalse(sender.confirmApplied(light, "mainpi/run/1", true));
            for (String type : List.of("LightCommand", "LightCommand", "LightStateQuery")) {
                var frame = frames.poll(3, TimeUnit.SECONDS);
                assertNotNull(frame);
                assertEquals(type, mapper.readTree(frame.getPayload()).path("type").asText());
            }
            assertEquals("NO_MATCHING_QUERY", sender.reconcileState(light, "wrong-query", "mainpi/run/1", true));
            assertEquals("MISMATCH", sender.reconcileState(light, "mainpi/run/1", lastApplied, on));
            sender.dispatch();
            assertNull(frames.poll(150, TimeUnit.MILLISECONDS));
            assertEquals(2, control.lightSnapshot().orElseThrow().pending().size());
        } finally {
            sender.stop();
        }
    }

    @Test
    void replyInsideSendMessageConfirmsWithoutRetransmitting() throws Exception {
        var mapper = new ObjectMapper();
        var control = new ControlService(2);
        var sessions = new DeviceSessions();
        var light = mock(WebSocketSession.class);
        when(light.getRemoteAddress()).thenReturn(new InetSocketAddress("127.0.0.1", 9000));
        when(light.isOpen()).thenReturn(true);
        sessions.register(light);
        var sender = new LightCommandSender(mapper, sessions, Optional.of(control), "127.0.0.1", 20);
        var receiver = Executors.newSingleThreadExecutor();
        var confirmed = new CompletableFuture<Boolean>();
        try {
            assertEquals("INITIALIZED", sender.initialize(light, "light-run", false));
            doAnswer(call -> {
                TextMessage message = call.getArgument(0);
                var payload = mapper.readTree(message.getPayload()).path("payload");
                var reply = receiver.submit(() -> sender.confirmApplied(light,
                        payload.path("commandId").asText(), payload.path("on").asBoolean()));
                confirmed.complete(reply.get(1, TimeUnit.SECONDS));
                return null;
            }).when(light).sendMessage(any());
            assertEquals(LightControl.Admission.ACCEPTED,
                    sender.acceptButtonPress(new ButtonEventId("mainpi", "run", 1)));
            sender.dispatch();

            assertTrue(confirmed.get(3, TimeUnit.SECONDS));
            verify(light, after(200).times(1)).sendMessage(any());
            assertTrue(control.lightSnapshot().orElseThrow().pending().isEmpty());
        } finally {
            sender.stop();
            receiver.shutdownNow();
        }
    }
}
