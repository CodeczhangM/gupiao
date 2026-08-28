package com.codec.quantserver.service;

import org.junit.jupiter.api.Test;

import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class CycleWatchSchedulerTest {

    @Test
    void scheduledMethodsSendExactSlotsAndKeepRunningAfterFailure() {
        QuantPythonClient client = mock(QuantPythonClient.class);
        CycleWatchScheduler scheduler = new CycleWatchScheduler(client);

        scheduler.run0935();
        scheduler.run1035();
        scheduler.run1125();
        scheduler.run1330();
        scheduler.run1430();
        scheduler.run1455();

        verify(client).checkCycleWatch(null, "0935");
        verify(client).checkCycleWatch(null, "1035");
        verify(client).checkCycleWatch(null, "1125");
        verify(client).checkCycleWatch(null, "1330");
        verify(client).checkCycleWatch(null, "1430");
        verify(client).checkCycleWatch(null, "1455");

        doThrow(new RuntimeException("python unavailable"))
                .when(client).checkCycleWatch(null, "0935");
        scheduler.run0935();
    }
}
