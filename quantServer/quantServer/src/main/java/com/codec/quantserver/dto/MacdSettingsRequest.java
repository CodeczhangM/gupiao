package com.codec.quantserver.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class MacdSettingsRequest {

    @JsonProperty("fast_period")
    private int fastPeriod;

    @JsonProperty("slow_period")
    private int slowPeriod;

    @JsonProperty("signal_period")
    private int signalPeriod;

    public int getFastPeriod() {
        return fastPeriod;
    }

    public void setFastPeriod(int fastPeriod) {
        this.fastPeriod = fastPeriod;
    }

    public int getSlowPeriod() {
        return slowPeriod;
    }

    public void setSlowPeriod(int slowPeriod) {
        this.slowPeriod = slowPeriod;
    }

    public int getSignalPeriod() {
        return signalPeriod;
    }

    public void setSignalPeriod(int signalPeriod) {
        this.signalPeriod = signalPeriod;
    }
}
