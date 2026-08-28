package com.codec.quantserver;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class QuantServerApplication {

	public static void main(String[] args) {
		SpringApplication.run(QuantServerApplication.class, args);
	}

}
