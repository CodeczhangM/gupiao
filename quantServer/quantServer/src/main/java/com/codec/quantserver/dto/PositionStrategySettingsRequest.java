package com.codec.quantserver.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.Map;

public class PositionStrategySettingsRequest {

    private Map<String, Object> pressure;
    private Map<String, Object> breakout;
    private Map<String, Object> distance;
    @JsonProperty("risk_reward")
    private Map<String, Object> riskReward;
    private Map<String, Object> network;

    public Map<String, Object> getPressure() { return pressure; }
    public void setPressure(Map<String, Object> pressure) { this.pressure = pressure; }

    public Map<String, Object> getBreakout() { return breakout; }
    public void setBreakout(Map<String, Object> breakout) { this.breakout = breakout; }

    public Map<String, Object> getDistance() { return distance; }
    public void setDistance(Map<String, Object> distance) { this.distance = distance; }

    public Map<String, Object> getRiskReward() { return riskReward; }
    public void setRiskReward(Map<String, Object> riskReward) { this.riskReward = riskReward; }

    public Map<String, Object> getNetwork() { return network; }
    public void setNetwork(Map<String, Object> network) { this.network = network; }
}
