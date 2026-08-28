package com.codec.quantserver.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class CycleWatchScheduler {
    private static final Logger LOGGER = LoggerFactory.getLogger(CycleWatchScheduler.class);
    private final QuantPythonClient client;

    public CycleWatchScheduler(QuantPythonClient client) {
        this.client = client;
    }

    @Scheduled(cron = "0 35 9 * * MON-FRI", zone = "Asia/Shanghai")
    public void run0935() { runSlot("0935"); }

    @Scheduled(cron = "0 35 10 * * MON-FRI", zone = "Asia/Shanghai")
    public void run1035() { runSlot("1035"); }

    @Scheduled(cron = "0 25 11 * * MON-FRI", zone = "Asia/Shanghai")
    public void run1125() { runSlot("1125"); }

    @Scheduled(cron = "0 30 13 * * MON-FRI", zone = "Asia/Shanghai")
    public void run1330() { runSlot("1330"); }

    @Scheduled(cron = "0 30 14 * * MON-FRI", zone = "Asia/Shanghai")
    public void run1430() { runSlot("1430"); }

    @Scheduled(cron = "0 55 14 * * MON-FRI", zone = "Asia/Shanghai")
    public void run1455() { runSlot("1455"); }

    private void runSlot(String slot) {
        try {
            client.checkCycleWatch(null, slot);
        } catch (RuntimeException exception) {
            LOGGER.warn("周期关注定时检查失败，slot={}", slot, exception);
        }
    }
}
