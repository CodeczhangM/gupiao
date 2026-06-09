package com.codec.quantserver.dto;

public class QuantBacktestRequest {

    private int lookbackDays = 30;
    private int holdDays = 3;
    private int limit = 20;

    public int getLookbackDays() {
        return lookbackDays;
    }

    public void setLookbackDays(int lookbackDays) {
        this.lookbackDays = lookbackDays;
    }

    public int getHoldDays() {
        return holdDays;
    }

    public void setHoldDays(int holdDays) {
        this.holdDays = holdDays;
    }

    public int getLimit() {
        return limit;
    }

    public void setLimit(int limit) {
        this.limit = limit;
    }
}
